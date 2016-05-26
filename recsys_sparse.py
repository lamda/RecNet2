# -*- coding: utf-8 -*-

from __future__ import division, print_function

import collections
import cPickle as pickle
import datetime
import itertools
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import numpy.matlib
np.matlib = numpy.matlib
import pdb
import scipy.sparse

np.set_printoptions(linewidth=225)
# np.seterr(all='raise')


class UtilityMatrix:
    def __init__(self, r, beta=1, hidden=None):
        self.beta = beta
        self.r = r  # rating matrix (=utility matrix)
        self.rt, self.hidden = self.get_training_data(hidden)
        self.mu, self.b_i, self.b_u = self.entrymean(self.rt)
        # hugo = self.rt.toarray().astype(float)
        # hugo[hugo == 0] = np.nan
        # mu = np.nanmean(hugo)
        # b_i = np.nanmean(hugo, axis=0) - mu
        # b_u = np.nanmean(hugo, axis=1) - mu
        self.coratings_r = self.get_coratings(self.r)
        self.coratings_rt = self.get_coratings(self.rt)
        self.s_r = self.get_similarities(self.r, self.coratings_r)
        self.s_rt = self.get_similarities(self.rt, self.coratings_rt)
        self.sirt_cache = {}
        self.rt_not_nan_indices = self.get_not_nan_indices(self.rt)
        self.rt_nan_indices = self.get_nan_indices(self.rt)
        # self.r_nan_indices = self.get_nan_indices(self.r)
        # self.r_not_nan_indices = self.get_not_nan_indices(self.r)

    def get_training_data(self, hidden, training_share=0.2):
        r_coo = self.r.tocoo(copy=True)
        if hidden is None:  # take some of the input as training data
            i, j = r_coo.row, r_coo.col
            rands = np.random.choice(
                len(i),
                int(training_share * len(i)),
                replace=False
            )
            hidden = np.vstack((i[rands], j[rands]))
        r_dok = r_coo.todok()
        for idx in range(hidden.shape[1]):
            del r_dok[hidden[0, idx], hidden[1, idx]]
        r_coo = r_dok.tocoo()
        r = r_coo.tocsr()
        return r, hidden

    def entrymean(self, m, axis=None):
        """Average a matrix over the given axis. If the axis is None,
        average over both rows and columns, returning a scalar.
        (via some SciPy function)
        """
        # Mimic numpy's casting.  The int32/int64 check works around numpy
        # 1.5.x behavior of np.issubdtype, see gh-2677.
        if (np.issubdtype(m.dtype, np.float_) or
                np.issubdtype(m.dtype, np.int_) or
                    m.dtype in [np.dtype('int32'), np.dtype('int64')] or
                np.issubdtype(m.dtype, np.bool_)):
            res_dtype = np.float_
        elif np.issubdtype(m.dtype, np.complex_):
            res_dtype = np.complex_
        else:
            res_dtype = m.dtype

        m = m.astype(res_dtype)
        mu = m.sum(None) / m.getnnz()
        # if user or item has no ratings (stripped from training data), set to 0
        b_i = m.sum(0)
        b_u = m.sum(1)
        with np.errstate(invalid='ignore'):
            b_i = (b_i / m.getnnz(axis=0)) - mu
            b_u = (b_u.T / m.getnnz(axis=1)) - mu
        b_i[np.isnan(b_i)] = 0
        b_u[np.isnan(b_u)] = 0

        return mu, b_i, b_u

    def get_coratings(self, r):
        # r_copy = np.copy(r)
        # r_copy[np.isnan(r_copy)] = 0
        # r_copy[r_copy > 0] = 1
        # coratings = r_copy.T.dot(r_copy)

        um = scipy.sparse.csr_matrix(r)
        um.data = np.ones(um.data.shape[0])
        coratings = um.T.dot(um)
        coratings.setdiag(0)
        return coratings

    def get_similarities(self, r, coratings):
        # # compute similarities
        # rc = r - np.nanmean(r, axis=0)  # ratings - item average
        # rc[np.isnan(rc)] = 0.0
        # # ignore division errors, set the resulting nans to zero
        # with np.errstate(all='ignore'):
        #     s = np.corrcoef(rc.T)
        # s[np.isnan(s)] = 0.0
        #
        # # shrink similarities
        # s = (coratings * s) / (coratings + self.beta)
        # return s

        print('centering...')
        # use the centered version for similarity computation
        um_centered = r.toarray().astype(np.float32)
        um_centered[np.where(um_centered == 0)] = np.nan
        um_centered = um_centered - np.nanmean(um_centered, axis=0)[np.newaxis, :]
        um_centered[np.where(np.isnan(um_centered))] = 0

        print('computing similarities...')
        A = scipy.sparse.csr_matrix(um_centered)

        print(1)
        # transpose, as the code below compares rows
        A = A.T

        print(2)
        # base similarity matrix (all dot products)
        similarity = A.dot(A.T)

        print(3)
        # squared magnitude of preference vectors (number of occurrences)
        square_mag = similarity.diagonal()

        print(4)
        # inverse squared magnitude
        inv_square_mag = 1 / square_mag

        print(5)
        # if it doesn't occur, set the inverse magnitude to 0 (instead of inf)
        inv_square_mag[np.isinf(inv_square_mag)] = 0

        print(6)
        # inverse of the magnitude
        inv_mag = np.sqrt(inv_square_mag)

        print(7)
        # cosine similarity (elementwise multiply by inverse magnitudes)
        col_ind = range(len(inv_mag))
        row_ind = np.zeros(len(inv_mag))
        inv_mag2 = scipy.sparse.csr_matrix((inv_mag, (col_ind, row_ind)))

        print(8)
        cosine = similarity.multiply(inv_mag2)

        print(9)
        cosine = cosine.T.multiply(inv_mag2)

        print(10)
        cosine.setdiag(0)

        s = cosine.toarray()
        # shrink similarities
        # this is "s = (coratings * s) / (coratings + self.beta)" in sparse form
        coratings_shrunk = scipy.sparse.csr_matrix((
            coratings.data / (coratings.data + self.beta),
            coratings.indices,
            coratings.indptr)
        )

        return scipy.sparse.csr_matrix(coratings_shrunk.multiply(s))

    def similar_items(self, u, i, k, use_all=False):
        try:
            return self.sirt_cache[(u, i, k)]
        except KeyError:
            if use_all:  # use entire matrix
                r_u = self.r[u, :]   # user ratings
                s_i = np.copy(self.s_r[i, :])  # item similarity
            else:  # use training matrix
                r_u = self.rt[u, :]  # user ratings
                s_i = np.copy(self.s_rt[i, :])  # item similarity
            # s_i[s_i < 0.0] = np.nan  # mask only to similar items
            s_i[i] = np.nan  # mask the item
            s_i[np.isnan(r_u)] = np.nan  # mask to items rated by the user
            nn = np.isnan(s_i).sum()  # how many invalid items

            s_i_sorted = np.argsort(s_i)
            s_i_k = tuple(s_i_sorted[-k - nn:-nn])
            self.sirt_cache[(u, i, k)] = s_i_k
            return s_i_k

    def get_not_nan_indices(self, m):
        # nnan = np.where(~np.isnan(m))
        # nnan_indices = zip(nnan[0], nnan[1])
        # return nnan_indices

        indices = m.nonzero()
        return zip(indices[0], indices[1])

    def get_nan_indices(self, m):
        # ynan = np.where(np.isnan(m))
        # ynan_indices = zip(ynan[0], ynan[1])
        # return ynan_indices

        # TODO!
        return [(-1, -1)]


class Recommender:
    def __init__(self, m):
        self.m = m
        self.rmse = []

    def predict_single(self, u, i, dbg=False):
        raise NotImplementedError

    def predict_all(self, r):
        raise NotImplementedError

    def training_error_single(self):
        sse = 0.0
        for u, i in self.m.rt_not_nan_indices:
            err = self.m.rt[u, i] - self.predict_single(u, i)
            sse += err ** 2
        return np.sqrt(sse / len(self.m.rt_not_nan_indices))

    def training_error_all(self):
        err = self.m.rt - self.predict_all(self.m.rt)
        sse = (err ** 2).sum()
        return np.sqrt(sse / self.rt.nnz)

    def training_error(self):
        return self.training_error_all()

    def test_error_single(self):
        sse = 0.0
        # errs = []
        for u, i in self.m.hidden.T:
            err = self.m.r[u, i] - self.predict_single(u, i)
            sse += err ** 2
            # errs.append(err)
            # print(self.m.r[u, i], self.predict(u, i), err)
            # pdb.set_trace()
            # if err > 100:
            #     print(err, self.m.r[u, i], self.predict(u, i))
            #     self.predict(u, i, dbg=True)

        # return np.sqrt(sse) / self.m.hidden.shape[1]
        # pdb.set_trace()
        return np.sqrt(sse / self.m.hidden.shape[1])

    def test_error_all(self):
        err = self.m.r - self.predict_all(self.m.r)
        sse = np.square(err).sum()
        return np.sqrt(sse / self.m.r.nnz)

    def test_error(self):
        return self.test_error_all()

    def print_test_error(self):
        print('%.3f - Test Error %s' %
              (self.test_error(), self.__class__.__name__))

    def plot_rmse(self, title='', suffix=None):
        plt.plot(range(len(self.rmse)), self.rmse)
        plt.xlabel("iteration")
        plt.ylabel("RMSE")
        plt.title(title + ' | ' + '%.4f' % self.rmse[-1] +
                  ' | ' + datetime.datetime.now().strftime("%H:%M:%S"))
        plt.savefig('rmse' + ('_' + suffix if suffix is not None else '') +
                    '.png')


class GlobalAverageRecommender(Recommender):
    def __init__(self, m):
        Recommender.__init__(self, m)

    def predict_single(self, u, i, dbg=False):
        return self.m.mu

    def predict_all(self, r):
        return np.ones(r.shape) * self.m.mu


class UserItemAverageRecommender(Recommender):
    def __init__(self, m):
        Recommender.__init__(self, m)

    def predict_single(self, u, i, dbg=False):
        # predict an item-based CF rating based on the training data
        b_xi = self.m.mu + self.m.b_u[u] + self.m.b_i[i]
        return b_xi

    def predict_all(self, r):
        P = np.ones(r.shape) * self.m.mu
        P += np.matlib.repmat(self.m.b_i, r.shape[0], 1)
        P += np.matlib.repmat(self.m.b_u.T, 1, r.shape[1])
        return P


class CFNN(Recommender):
    def __init__(self, m, k):
        Recommender.__init__(self, m)
        self.w = self.m.s_rt
        self.k = k
        self.normalize = True
        print('k =', k)

    def predict_basic_old(self, u, i, dbg=False):
        pdb.set_trace()
        n_u_i = self.m.similar_items(u, i, self.k)
        r = 0
        for j in n_u_i:
            if self.w[i, j] < 0:  # resolve problems with near-zero weight sums
                continue
            r += self.w[i, j] * self.m.r[u, j]
        if self.normalize:
            if r != 0:
                s = sum(self.w[i, j] for j in n_u_i
                        # resolve problems with near-zero weight sums
                        if self.w[i, j] > 0
                        )
                if not np.isfinite(r/sum(self.w[i, j] for j in n_u_i)) or\
                        np.isnan(r/sum(self.w[i, j] for j in n_u_i)):
                    pdb.set_trace()
                r /= s
        return r

    def predict_single(self, u, i, dbg=False):
        # predict an item-based CF rating based on the training data
        b_xi = self.m.mu + self.m.b_u[u] + self.m.b_i[i]
        if np.isnan(b_xi):
            if np.isnan(self.m.b_u[u]) and np.isnan(self.m.b_i[i]):
                return self.m.mu
            elif np.isnan(self.m.b_u[u]):
                return self.m.mu + self.m.b_i[i]
            else:
                return self.m.mu + self.m.b_u[u]

        n_u_i = self.m.similar_items(u, i, self.k)
        r = 0
        for j in n_u_i:
            if self.w[i, j] < 0:  # resolve problems with near-zero weight sums
                continue
            diff = self.m.r[u, j] - (self.m.mu + self.m.b_u[u] + self.m.b_i[j])
            r += self.w[i, j] * diff
        if dbg:
            print('r =', r)
            print('r (normalized) =', r / sum(self.w[i, j] for j in n_u_i))
            s = sum(self.w[i, j] for j in n_u_i)
            print('s =', s)
            pdb.set_trace()
        if self.normalize:
            if r != 0:
                s = sum(self.w[i, j] for j in n_u_i
                        # resolve problems with near-zero weight sums
                        if self.w[i, j] > 0
                        )
                if not np.isfinite(r/sum(self.w[i, j] for j in n_u_i)) or\
                        np.isnan(r/sum(self.w[i, j] for j in n_u_i)):
                    pdb.set_trace()
                r /= s
        return b_xi + r

    def predict_all(self, r):
        P = np.ones(r.shape) * self.m.mu
        P += np.matlib.repmat(self.m.b_i, r.shape[0], 1)
        P += np.matlib.repmat(self.m.b_u.T, 1, r.shape[1])


class Factors(Recommender):
    def __init__(self, m, k, eta_type, nsteps=500, eta=0.000004,
                 regularize=False, newton=False, tol=0.5*1e-5, lamda=0.05,
                 init='random', reset_params='False'):
        Recommender.__init__(self, m)
        self.k = k
        self.nsteps = nsteps
        self.eta = eta
        self.eta_type = eta_type
        self.regularize = regularize
        self.newton = newton
        self.tol = tol
        self.lamda = lamda
        self.reset_params = reset_params

        if init == 'svd':
            # init by Singular Value Decomposition
            m = self.m.rt
            m[np.where(np.isnan(m))] = 0
            ps, ss, vs = np.linalg.svd(m)
            self.p = ps[:, :self.k]
            self.q = np.dot(np.diag(ss[:self.k]), vs[:self.k, :]).T
        elif init == 'random':
            # init randomly
            self.eta *= 15  # use a higher eta for random initialization
            self.p = np.random.random((self.m.rt.shape[0], self.k))
            self.q = np.random.random((self.m.rt.shape[1], self.k))
        elif init == 'random_small':
            self.eta *= 100  # use a higher eta for random initialization
            self.p = np.random.random((self.m.rt.shape[0], self.k)) / 100
            self.q = np.random.random((self.m.rt.shape[1], self.k)) / 100
        elif init == 'zeros':
            self.p = np.zeros((self.m.rt.shape[0], self.k))
            self.q = np.zeros((self.m.rt.shape[1], self.k))
        else:
            print('init method not supported')
            pdb.set_trace()

        print('init =', init)
        print('k =', k)
        print('lamda =', self.lamda)
        print('eta = ', self.eta)
        print('eta_type = ', self.eta_type)

        self.factorize()
        # self.factorize_biased()

        print('init =', init)
        print('k =', k)
        print('lamda =', self.lamda)
        print('eta = ', self.eta)
        print('eta_type = ', self.eta_type)

        # self.plot_rmse('%.4f' % diff, suffix='init')
        print('test error: %.4f' % self.test_error())

    def predict(self, u, i, dbg=False):
        p_u = self.p[u, :]
        q_i = self.q[i, :]
        return np.dot(p_u, q_i.T)

    def predict_biased(self, u, i):
        b_xi = self.m.mu + self.m.b_u[u] + self.m.b_i[i]
        if np.isnan(b_xi):
            if np.isnan(self.m.b_u[u]) and np.isnan(self.m.b_i[i]):
                return self.m.mu
            elif np.isnan(self.m.b_u[u]):
                return self.m.mu + self.m.b_i[i]
            else:
                return self.m.mu + self.m.b_u[u]
        p_u = self.p[u, :]
        q_i = self.q[i, :]
        return b_xi + np.dot(p_u, q_i.T)

    def factorize(self):
        test_rmse = []
        for m in xrange(self.nsteps):
            masked = np.ma.array(self.m.rt, mask=np.isnan(self.m.rt))
            err = np.dot(self.p, self.q.T) - masked
            delta_p = np.ma.dot(err, self.q)
            delta_q = np.ma.dot(err.T, self.p)

            if self.regularize:
                delta_p += self.lamda * self.p
                delta_q += self.lamda * self.q

            self.p -= 2 * self.eta * delta_p
            self.q -= 2 * self.eta * delta_q

            self.rmse.append(self.training_error())
            # print(m, 'eta = %.8f, rmse = %.8f' % (self.eta, self.rmse[-1]))
            print(m, 'eta = %.8f, training_rmse = %.8f, test_rmse = %.8f' %
                  (self.eta, self.rmse[-1], self.test_error()))
            if len(self.rmse) > 1:
                if abs(self.rmse[-1] - self.rmse[-2]) < self.tol:
                    break
                if self.rmse[-1] > self.rmse[-2]:
                    print('RMSE getting larger')
                    if self.reset_params:
                        self.p += 2 * self.eta * delta_p  # reset parameters
                        self.q += 2 * self.eta * delta_q  # reset parameters
                        del self.rmse[-1]  # reset last error value
                    self.eta *= 0.5
                    if self.eta_type == 'constant':
                        break
                    elif self.eta_type == 'increasing':
                        break
                else:
                    if self.eta_type == 'constant':
                        pass
                    else:  # 'increasing' or 'bold_driver'
                        self.eta *= 1.1
            if (m % 100) == 0:
                test_rmse.append(self.test_error())
                print('    TEST RMSE:')
                for idx, err in enumerate(test_rmse):
                    print('        %d | %.8f' % (idx * 100, err))
        print('    TEST RMSE:')
        for idx, err in enumerate(test_rmse):
            print('        %d | %.8f' % (idx * 100, err))

    def factorize_biased(self):
        self.predict = self.predict_biased
        ucount = self.m.rt.shape[0]
        icount = self.m.rt.shape[1]
        B_u = np.tile(self.m.b_u, (icount, 1)).T
        B_i = np.tile(self.m.b_i, (ucount, 1))
        for m in xrange(self.nsteps):
            masked = np.ma.array(self.m.rt, mask=np.isnan(self.m.rt))
            err = np.dot(self.p, self.q.T) + self.m.mu + B_u + B_i - masked
            delta_p = np.ma.dot(err, self.q)
            delta_q = np.ma.dot(err.T, self.p)

            if self.regularize:
                delta_p += self.lamda * self.p
                delta_q += self.lamda * self.q

            self.p -= 2 * self.eta * delta_p
            self.q -= 2 * self.eta * delta_q

            self.rmse.append(self.training_error())
            # print(m, 'eta = %.8f, rmse = %.8f' % (self.eta, self.rmse[-1]))
            print(m, 'eta = %.8f, training_rmse = %.8f, test_rmse = %.8f' %
                  (self.eta, self.rmse[-1], self.test_error()))
            if len(self.rmse) > 1:
                if abs(self.rmse[-1] - self.rmse[-2]) < self.tol:
                    break
                if self.rmse[-1] > self.rmse[-2]:
                    print('RMSE getting larger')
                    self.p += 2 * self.eta * delta_p  # reset parameters
                    self.q += 2 * self.eta * delta_q  # reset parameters
                    del self.rmse[-1]  # reset last error value
                    self.eta *= 0.5
                    if self.eta_type == 'constant':
                        break
                    elif self.eta_type == 'increasing':
                        break
                else:
                    if self.eta_type == 'constant':
                        pass
                    else:  # 'increasing' or 'bold_driver'
                        self.eta *= 1.1

    def factorize_iterate(self):
        for m in xrange(self.nsteps):
            print(m, end='\r')
            delta_p = np.zeros((self.m.rt.shape[0], self.k))
            delta_q = np.zeros((self.m.rt.shape[1], self.k))

            for u, i in self.m.rt_not_nan_indices:
                error = np.dot(self.p[u, :], self.q[i, :]) - self.m.rt[u, i]
                for k in range(self.k):
                    delta_p[u, k] = error * self.p[u, k]
                    delta_q[i, k] = error * self.q[i, k]

            self.p -= 2 * self.eta * delta_p
            self.q -= 2 * self.eta * delta_q

            self.rmse.append(self.training_error())
            print(self.rmse[-1])
            if len(self.rmse) > 1:
                if abs(self.rmse[-1] - self.rmse[-2]) < self.tol:
                    break


class WeightedCFNN(CFNN):
    def __init__(self, m, k, eta_type, init, nsteps=500, eta=0.00075,
                 tol=0.5*1e-5, lamda=0.05, regularize=False):
        Recommender.__init__(self, m)
        self.k = k
        self.nsteps = nsteps
        self.eta = eta
        self.eta_type = eta_type
        self.tol = tol
        self.regularize = regularize
        self.lamda = lamda
        self.normalize = False
        if init == 'sim':
            self.w = np.copy(self.m.s_rt)
        elif init == 'random':
            self.w = np.random.random((self.m.rt.shape[1], self.m.rt.shape[1]))
        elif init == 'zeros':
            self.w = np.zeros((self.m.rt.shape[1], self.m.rt.shape[1]))
        else:
            print('init method not supported')
        # w_init = np.copy(self.w)

        print('init =', init)
        print('k =', k)
        print('lamda =', self.lamda)
        print('eta =', self.eta)
        print('eta_type =', self.eta_type)

        self.interpolate_weights_old()

        print('init =', init)
        print('k =', k)
        print('lamda =', self.lamda)
        print('eta = ', self.eta)
        print('eta_type =', self.eta_type)

        # diff = np.linalg.norm(w_init - self.w)

        # self.plot_rmse('%.4f' % diff, suffix=init)
        print(self.__class__.__name__)
        print('test error: %.4f' % self.test_error())

    def interpolate_weights_old(self):
        print('ATTENTION not resetting larger values')
        icount = self.m.rt.shape[1]

        rt_nan_indices = set(self.m.rt_nan_indices)
        ucount = self.m.rt.shape[0]
        m = self.m
        test_rmse = []
        for step in xrange(self.nsteps):
            print(step, end='\r')
            delta_w_i_j = np.zeros((icount, icount))
            for i in xrange(icount):
                for u in xrange(ucount):
                    if (u, i) in rt_nan_indices:
                        continue
                    s_u_i = m.similar_items(u, i, self.k)
                    error = sum(self.w[i, k] * m.rt[u, k] for k in s_u_i) -\
                            m.rt[u, i]
                    for j in s_u_i:
                        delta_w_i_j[i, j] += error * m.rt[u, j]
                        if self.regularize:
                            delta_w_i_j[i, j] += self.lamda * self.w[i, j]
            self.w -= 2 * self.eta * delta_w_i_j
            self.rmse.append(self.training_error())
            print(step, 'eta = %.8f, rmse = %.8f' % (self.eta, self.rmse[-1]))
            if len(self.rmse) > 1:
                if abs(self.rmse[-1] - self.rmse[-2]) < self.tol:
                    break
                if self.rmse[-1] > self.rmse[-2]:
                    print('RMSE getting larger')
                    # self.w += 2 * self.eta * delta_w_i_j  # reset parameters
                    # del self.rmse[-1]
                    self.eta *= 0.5
                    if self.eta_type == 'constant':
                        break
                    elif self.eta_type == 'increasing':
                        break
                else:
                    if self.eta_type == 'constant':
                        pass
                    else:  # 'increasing' or 'bold_driver'
                        self.eta *= 1.05
            if (step % 100) == 0:
                test_rmse.append(self.test_error())
                print('    TEST RMSE:')
                for idx, err in enumerate(test_rmse):
                    print('        %d | %.8f' % (idx * 100, err))
        print('ATTENTION not resetting larger values')
        print('    TEST RMSE:')
        for idx, err in enumerate(test_rmse):
            print('        %d | %.8f' % (idx * 100, err))

    def interpolate_weights_new(self):
        rt_nan_indices = set(self.m.rt_nan_indices)
        ucount = self.m.rt.shape[0]
        icount = self.m.rt.shape[1]
        B_u = np.tile(self.m.b_u, (icount, 1)).T
        B_i = np.tile(self.m.b_i, (ucount, 1))
        m = self.m
        m.b = self.m.mu + B_u + B_i
        m.rtb = self.m.rt - m.b
        for step in xrange(self.nsteps):
            print(step, end='\r')
            delta_w_i_j = np.zeros((icount, icount))
            for i in xrange(icount):
                for u in xrange(ucount):
                    if (u, i) in rt_nan_indices:
                        continue
                    s_u_i = m.similar_items(u, i, self.k)
                    error = m.b[u, i] - m.rt[u, i] +\
                        sum(self.w[i, k] * m.rtb[u, k] for k in s_u_i)
                    for j in s_u_i:
                        delta_w_i_j[i, j] += error * m.rtb[u, j]
                        if self.regularize:
                            delta_w_i_j[i, j] += self.lamda * self.w[i, j]
            self.w -= 2 * self.eta * delta_w_i_j
            self.rmse.append(self.training_error())
            print(step, 'eta = %.8f, rmse = %.8f' % (self.eta, self.rmse[-1]))
            if len(self.rmse) > 1:
                if abs(self.rmse[-1] - self.rmse[-2]) < self.tol:
                    break
                if self.rmse[-1] > self.rmse[-2]:
                    print('RMSE getting larger')
                    self.w += 2 * self.eta * delta_w_i_j  # reset parameters
                    self.eta *= 0.5
                    del self.rmse[-1]
                    if self.eta_type == 'constant':
                        break
                    elif self.eta_type == 'increasing':
                        break
                else:
                    if self.eta_type == 'constant':
                        pass
                    else:  # 'increasing' or 'bold_driver'
                        self.eta *= 1.1


class WeightedCFNNUnbiased(CFNN):
    def __init__(self, m, k, regularize, eta, eta_type, init,
                 nsteps=1000, tol=1e-5, lamda=0.05):
        Recommender.__init__(self, m)
        self.k = k
        self.nsteps = nsteps
        self.eta = eta
        self.eta_type = eta_type
        self.tol = tol
        self.normalize = False
        self.regularize = regularize
        self.lamda = lamda
        if init == 'sim':
            self.w = np.copy(self.m.s_rt)
        elif init == 'random':
            self.w = np.random.random((self.m.rt.shape[1], self.m.rt.shape[1]))/100
        elif init == 'zeros':
            self.w = np.zeros((self.m.rt.shape[1], self.m.rt.shape[1]))
        else:
            print('init method not supported')

        print('k =', k)
        print('eta =', self.eta)
        print('eta_type =', self.eta_type)
        print('init = ', init)

        self.interpolate_weights()

        print('k =', k)
        print('eta = ', self.eta)
        print('eta_type =', self.eta_type)
        print('init = ', init)
        print(self.__class__.__name__)
        print('test error: %.4f' % self.test_error())

    def predict(self, u, i, dbg=False):
        n_u_i = self.m.similar_items(u, i, self.k)
        r = sum(self.w[i, j] * self.m.r[u, j] for j in n_u_i)
        if self.normalize and r > 0:
            r /= sum(self.w[i, j] for j in n_u_i)
        return r

    def interpolate_weights(self):
        icount = self.m.rt.shape[1]
        rt_nan_indices = set(self.m.rt_nan_indices)
        ucount = self.m.rt.shape[0]
        m = self.m
        for step in xrange(self.nsteps):
            print(step, end='\r')
            delta_w_i_j = np.zeros((icount, icount))
            for i in xrange(icount):
                for u in xrange(ucount):
                    if (u, i) in rt_nan_indices:
                        continue
                    s_u_i = m.similar_items(u, i, self.k)
                    error = sum(self.w[i, k] * m.rt[u, k] for k in s_u_i) -\
                            m.rt[u, i]
                    for j in s_u_i:
                        delta_w_i_j[i, j] += error * m.rt[u, j]
                        if self.regularize:
                            delta_w_i_j[i, j] += self.lamda * self.w[i, j]

            # # update weights
            self.w -= 2 * self.eta * delta_w_i_j

            # # ensure weights >= 0
            self.w[self.w < 0] = 0

            self.rmse.append(self.training_error())
            print(step, 'eta = %.8f, training_rmse = %.8f, test_rmse = %.8f' %
                  (self.eta, self.rmse[-1], self.test_error()))
            if len(self.rmse) > 1:
                if abs(self.rmse[-1] - self.rmse[-2]) < self.tol:
                    break
                if self.rmse[-1] > self.rmse[-2]:
                    print('RMSE getting larger')
                    self.w += 2 * self.eta * delta_w_i_j  # reset parameters
                    del self.rmse[-1]
                    self.eta *= 0.5
                    if self.eta_type == 'constant':
                        break
                    elif self.eta_type == 'increasing':
                        break
                else:
                    if self.eta_type == 'constant':
                        pass
                    else:  # 'increasing' or 'bold_driver'
                        self.eta *= 1.05


class WeightedCFNNBiased(CFNN):
    def __init__(self, m, k, eta_type, init, nsteps=500, eta=0.00075,
                 tol=1e-5, lamda=0.05, regularize=False):
        Recommender.__init__(self, m)
        self.k = k
        self.nsteps = nsteps
        self.eta = eta
        self.eta_type = eta_type
        self.tol = tol
        self.regularize = regularize
        self.lamda = lamda
        self.normalize = False
        if init == 'sim':
            self.w = np.copy(self.m.s_rt)
        elif init == 'random':
            self.w = np.random.random((self.m.rt.shape[1], self.m.rt.shape[1]))
        elif init == 'zeros':
            self.w = np.zeros((self.m.rt.shape[1], self.m.rt.shape[1]))
        else:
            print('init method not supported')
            pdb.set_trace()
        # w_init = np.copy(self.w)

        print('init =', init)
        print('k =', k)
        print('lamda =', self.lamda)
        print('eta =', self.eta)
        print('eta_type =', self.eta_type)

        self.interpolate_weights()

        print('init =', init)
        print('k =', k)
        print('lamda =', self.lamda)
        print('eta = ', self.eta)
        print('eta_type =', self.eta_type)

        # diff = np.linalg.norm(w_init - self.w)

        # self.plot_rmse('%.4f' % diff, suffix=init)
        print(self.__class__.__name__)
        print('test error: %.4f' % self.test_error())

    def predict(self, u, i, dbg=False):
        # predict an item-based CF rating based on the training data
        b_xi = self.m.mu + self.m.b_u[u] + self.m.b_i[i]
        if np.isnan(b_xi):
            if np.isnan(self.m.b_u[u]) and np.isnan(self.m.b_i[i]):
                return self.m.mu
            elif np.isnan(self.m.b_u[u]):
                return self.m.mu + self.m.b_i[i]
            else:
                return self.m.mu + self.m.b_u[u]

        n_u_i = self.m.similar_items(u, i, self.k)
        r = 0
        for j in n_u_i:
            diff = self.m.r[u, j] - (self.m.mu + self.m.b_u[u] + self.m.b_i[j])
            r += self.w[i, j] * diff
        if self.normalize:
            if r != 0:
                s = sum(self.w[i, j] for j in n_u_i)
                if not np.isfinite(r/sum(self.w[i, j] for j in n_u_i)) or\
                        np.isnan(r/sum(self.w[i, j] for j in n_u_i)):
                    pdb.set_trace()
                r /= s
        return b_xi + r

    def interpolate_weights(self):
        rt_nan_indices = set(self.m.rt_nan_indices)
        ucount = self.m.rt.shape[0]
        icount = self.m.rt.shape[1]
        B_u = np.tile(self.m.b_u, (icount, 1)).T
        B_i = np.tile(self.m.b_i, (ucount, 1))
        m = self.m
        m.b = self.m.mu + B_u + B_i
        m.rtb = self.m.rt - m.b
        for step in xrange(self.nsteps):
            print(step, end='\r')
            delta_w_i_j = np.zeros((icount, icount))
            for i in xrange(icount):
                for u in xrange(ucount):
                    if (u, i) in rt_nan_indices:
                        continue
                    s_u_i = m.similar_items(u, i, self.k)
                    error = m.b[u, i] - m.rt[u, i] +\
                        sum(self.w[i, k] * m.rtb[u, k] for k in s_u_i)
                    for j in s_u_i:
                        delta_w_i_j[i, j] += error * m.rtb[u, j]
                        if self.regularize:
                            delta_w_i_j[i, j] += self.lamda * self.w[i, j]
            self.w -= 2 * self.eta * delta_w_i_j
            self.rmse.append(self.training_error())
            print(step, 'eta = %.8f, training_rmse = %.8f, test_rmse = %.8f' %
                  (self.eta, self.rmse[-1], self.test_error()))
            if len(self.rmse) > 1:
                if abs(self.rmse[-1] - self.rmse[-2]) < self.tol:
                    break
                if self.rmse[-1] > self.rmse[-2]:
                    print('RMSE getting larger')
                    self.w += 2 * self.eta * delta_w_i_j  # reset parameters
                    self.eta *= 0.5
                    del self.rmse[-1]
                    if self.eta_type == 'constant':
                        break
                    elif self.eta_type == 'increasing':
                        break
                else:
                    if self.eta_type == 'constant':
                        pass
                    else:  # 'increasing' or 'bold_driver'
                        self.eta *= 1.1


def read_movie_lens_data():
    import csv
    csvfile = open('u1.test', 'r')
    reader = csv.reader(csvfile, delimiter='\t')
    ratings = {}
    movies = set()
    for row in reader:
        user = row[0]
        movie = row[1]
        rating = row[2]
        if user not in ratings:
            ratings[user] = [(movie, rating)]
        else:
            ratings[user].append((movie, rating))
        movies.add(movie)

    m = list(movies)
    r = np.zeros([len(ratings), len(movies)])
    r.fill(np.nan)
    i = 0
    for user in ratings:
        uratings = ratings[user]
        for rating in uratings:
            r[i, m.index(rating[0])] = rating[1]
        i += 1

    return r


if __name__ == '__main__':
    start_time = datetime.datetime.now()

    # complete MovieLens matrix
    # with open('um_bookcrossing.obj', 'rb') as infile:
    # with open('um_imdb.obj', 'rb') as infile:
    #     m = pickle.load(infile).astype(float)

    # m = np.load('data/imdb/recommendation_data/RatingBasedRecommender_um_sparse.obj.npy')
    # m = np.load('data/movielens/recommendation_data/RatingBasedRecommender_um_sparse.obj.npy')
    # m = m.item()
    # m = m.astype('int32')


    # pdb.set_trace()
    # m = m.astype(float)

    # m[m == 0] = np.nan

    # with open('m255.obj', 'rb') as infile: # sample of 255 from MovieLens
    #     m = pickle.load(infile).astype(float)
    # m[m == 0] = np.nan

    # m = read_movie_lens_data() # Denis's MovieLens sample

    m = scipy.sparse.csr_matrix(np.array([  # simple test case
        [5, 1, 0, 2, 2, 4, 3, 2],
        [1, 5, 2, 5, 5, 1, 1, 4],
        [2, 0, 3, 5, 4, 1, 2, 4],
        [4, 3, 5, 3, 0, 5, 3, 0],
        [2, 0, 1, 3, 0, 2, 5, 3],
        [4, 1, 0, 1, 0, 4, 3, 2],
        [4, 2, 1, 1, 0, 5, 4, 1],
        [5, 2, 2, 0, 2, 5, 4, 1],
        [4, 3, 3, 0, 0, 4, 3, 0]
    ]))
    hidden = np.array([
        [6, 2, 0, 2, 2, 5, 3, 0, 1, 1],
        [1, 2, 0, 4, 5, 3, 2, 3, 0, 4]
    ])
    um = UtilityMatrix(m, hidden=hidden)

    # m = np.array([  # simple test case 2
    #     [1, 5, 5, np.NAN, np.NAN, np.NAN],
    #     [2, 4, 3, np.NAN, np.NAN, np.NAN],
    #     [1, 4, 5, np.NAN, np.NAN, np.NAN],
    #     [1, 5, 5, np.NAN, np.NAN, np.NAN],
    #
    #     [np.NAN, np.NAN, np.NAN, 1, 2, 3],
    #     [np.NAN, np.NAN, np.NAN, 2, 1, 3],
    #     [np.NAN, np.NAN, np.NAN, 3, 2, 2],
    #     [np.NAN, np.NAN, np.NAN, 4, 3, 3],
    # ])
    # hidden = np.array([
    #     [0, 1, 3, 4, 5],
    #     [1, 2, 0, 4, 5]
    # ])
    # um = UtilityMatrix(m, hidden=hidden)

    # um = UtilityMatrix(m)

    # cfnn = CFNN(um, k=5); cfnn.print_test_error()
    # f = Factors(um, k=5, nsteps=500, eta_type='increasing', regularize=True, eta=0.00001, init='random')
    # w = WeightedCFNN(um, eta_type='increasing', k=5, eta=0.000001, regularize=True, init='random')
    # w = WeightedCFNN(um, eta_type='increasing', k=5, eta=0.001, regularize=True, init_sim=True)
    # w = WeightedCFNN(um, eta_type='bold_driver', k=5, eta=0.001, regularize=True, init_sim=False)

    # gar = GlobalAverageRecommender(um); gar.print_test_error()
    # uiar = UserItemAverageRecommender(um); uiar.print_test_error()
    for k in [
        1,
        2,
        5,
        # 10,
        # 15,
        # 20,
        # 25,
        # 40,
        # 50,
        # 60,
        # 80,
        # 100
    ]:
        cfnn = CFNN(um, k=k); cfnn.print_test_error()

    # wf = WeightedCFNNUnbiased(um, k=5, eta=0.0001, regularize=True,
    #                           eta_type='bold_driver', init='random')
    # wf = WeightedCFNNBiased(um, eta_type='bold_driver', k=5, eta=0.00001, init='random')


    # errors = []
    # for i in range(10):
    #     um = UtilityMatrix(m)
    # w = WeightedCFNN(um, k=5, eta=0.000001, regularize=True, init_sim=False)
    #     # w = WeightedCFNNUnbiased(um, k=5, eta=0.000001, regularize=True, init_sim=False)
    #     errors.append(w.test_error())
    # print('\nMean Error for WeightedCFNN:', np.mean(errors))

    end_time = datetime.datetime.now()
    print('Duration: {}'.format(end_time - start_time))

