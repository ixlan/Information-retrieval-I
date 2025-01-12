__author__ = 'arthur'

import itertools
import numpy as np
import lasagne
import theano
import theano.tensor as T
import time
from itertools import count
import query
import math
import copy

from support import sigmoid,NDCG, getRankedList

NUM_EPOCHS = 500

BATCH_SIZE = 1000
NUM_HIDDEN_UNITS = 200
LEARNING_RATE = 0.025
MOMENTUM = 0.95


THEANO_FLAGS="floatX=float32"

# TOD: Implement the lambda loss function
def lambda_loss(output, lambdas):
    return -output*lambdas


class LambdaRank:

    NUM_INSTANCES = count()

    def __init__(self, feature_count):
        self.feature_count = feature_count
        self.output_layer = self.build_model(feature_count,1,BATCH_SIZE)
        self.iter_funcs = self.create_functions(self.output_layer)
        self.ndcg=NDCG(1)

    # train_queries are what load_queries returns - implemented in query.py
    def train_with_queries(self, train_queries, num_epochs):

        try:
            now = time.time()
            for epoch in self.train(train_queries):
                if epoch['number'] % 20 == 0:
                    print("Epoch {} of {} took {:.3f}s".format(
                    epoch['number'], num_epochs, time.time() - now))
                    print("training loss:\t\t{:.6f}\n".format(epoch['train_loss']))
                    now = time.time()
                if epoch['number'] >= num_epochs:
                    break
        except KeyboardInterrupt:
            pass

    def score(self, query):
        feature_vectors = query.get_feature_vectors()
        scores = self.iter_funcs['out'](feature_vectors)
        return scores


    def build_model(self,input_dim, output_dim,
                    batch_size=BATCH_SIZE):
        """Create a symbolic representation of a neural network with `intput_dim`
        input nodes, `output_dim` output nodes and `num_hidden_units` per hidden
        layer.

        The training function of this model must have a mini-batch size of
        `batch_size`.

        A theano expression which represents such a network is returned.
        """
        print "input_dim",input_dim, "output_dim",output_dim
        l_in = lasagne.layers.InputLayer(
            shape=(batch_size, input_dim),
        )

        l_hidden = lasagne.layers.DenseLayer(
            l_in,
            num_units=200,
            nonlinearity=lasagne.nonlinearities.tanh,
        )


        l_out = lasagne.layers.DenseLayer(
            l_hidden,
            num_units=output_dim,
            nonlinearity=lasagne.nonlinearities.linear,
        )

        return l_out

    # Create functions to be used by Theano for scoring and training
    def create_functions(self, output_layer,
                          X_tensor_type=T.matrix,
                          batch_size=BATCH_SIZE,
                          learning_rate=LEARNING_RATE, momentum=MOMENTUM, L1_reg=0.0000005, L2_reg=0.000003):
        """Create functions for training, validation and testing to iterate one
           epoch.
        """
        X_batch = X_tensor_type('x')
        y_batch = T.fvector('y')

        output_row = lasagne.layers.get_output(output_layer, X_batch, dtype="float32")
        output = output_row.T

        output_row_det = lasagne.layers.get_output(output_layer, X_batch,deterministic=True, dtype="float32")

        # TOD: Change loss function
        # Point-wise loss function (squared error) - comment it out
        # loss_train = lasagne.objectives.squared_error(output,y_batch)
        # Pairwise loss function - comment it in
        loss_train = lambda_loss(output, y_batch)

        loss_train = loss_train.mean()

        # TODO: (Optionally) You can add regularization if you want - for those interested
        L1_loss = lasagne.regularization.regularize_network_params(output_layer,lasagne.regularization.l1)
        L2_loss = lasagne.regularization.regularize_network_params(output_layer,lasagne.regularization.l2)
        loss_train = loss_train.mean() + L1_loss * L1_reg + L2_loss * L2_reg

        # Parameters you want to update
        all_params = lasagne.layers.get_all_params(output_layer)

        # Update parameters, adam is a particular "flavor" of Gradient Descent
        updates = lasagne.updates.adam(loss_train, all_params,learning_rate)


        # Create two functions:

        # (1) Scoring function, deterministic, does not update parameters, outputs scores
        score_func = theano.function(
            [X_batch], output_row_det,
        )

        # (2) Training function, updates the parameters, outpust loss
        train_func = theano.function(
            [X_batch,y_batch], loss_train,
            updates=updates,
            # givens={
            #     X_batch: dataset['X_train'][batch_slice],
            #     # y_batch: dataset['y_valid'][batch_slice],
            # },
        allow_input_downcast=True)

        print "finished create_iter_functions"
        return dict(
            train=train_func,
            out=score_func,
        )


    # I assume that this one has to return a list of \lambda_i, i.e. a lambda for each document
    def lambda_function(self, labels, scores):
        I=self.__get_I(labels)
        n=len(labels)
        lamb=np.zeros(n,dtype='float32')
        for i in range(n):
            res=0 # left hand side lambda in \lambda_{i} equation
            if (i in I['left']):
                els=I['left'][i]
                for el in els:
                    labels[i],labels[el]=labels[el],labels[i] # swapping labels
                    ndcgDiff=np.abs(self.ndcg.run(labels,max_c=np.sum(labels))-self.defNDCG)
                    labels[el],labels[i]=labels[i],labels[el] # swapping labels back
                    s = (scores[i], scores[el])
                    res += self.__compute_lambda(s)*ndcgDiff
            if (i in I['right']):
                els=I['right'][i]
                for el in els:
                    labels[i],labels[el]=labels[el],labels[i] # swapping labels
                    ndcgDiff=np.abs(self.ndcg.run(labels,max_c=np.sum(labels))-self.defNDCG)
                    labels[el],labels[i]=labels[i],labels[el] # swapping labels back
                    s = (scores[el], scores[i])
                    res -= self.__compute_lambda(s)*ndcgDiff
            lamb[i]=res
        return lamb



    # scores for i,j tuple
    def __compute_lambda(self,scores):
        sig=sigmoid(scores[0]-scores[1])
        res=(-1/(1+math.exp(sig)))
        return sigmoid(res)

    # this is the runtime problem of the model
    def __get_I(self, labels):
        I_left = {}  # that is i appears to the left e.g. (i,j), (i,k) so {i:[j,k]}
        I_right = {}  # same principle
        n = len(labels)
        for i in range(n):
            for j in range(n):
                if (labels[i] > labels[j]):
                    if (i not in I_left):
                        I_left[i] = []
                    I_left[i].append(j)
                if (labels[j] > labels[i]):
                    if (i not in I_right):
                        I_right[i] = []
                    I_right[i].append(j)
        return {'left': I_left, 'right': I_right}

    def compute_lambdas_theano(self,query, labels):
        scores = self.score(query).flatten()
        result = self.lambda_function(labels,scores[:len(labels)])
        return result

    def train_once(self, X_train, query, labels):

        self.defNDCG=self.ndcg.run(labels,max_c=np.sum(labels)) # to avoid re-computation

        # TOD: Comment out to obtain the lambdas
        lambdas = self.compute_lambdas_theano(query,labels)
        #lambdas.resize((BATCH_SIZE, ))

        # Otherwise it breaks the whole code
        #X_train.resize((BATCH_SIZE, self.feature_count),refcheck=False)

        # TOD: Comment out (and comment in) to replace labels by lambdas
        batch_train_loss = self.iter_funcs['train'](X_train, lambdas)

        # this functions seems to do the actual training
        # batch_train_loss = self.iter_funcs['train'](X_train, labels)
        return batch_train_loss

    def train(self, train_queries):
        print('training LambdaRank')
        X_trains = train_queries.get_feature_vectors()

        queries = train_queries.values()

        for epoch in itertools.count(1):
            batch_train_losses = []
            random_batch = np.arange(len(queries))
            np.random.shuffle(random_batch)
            for index in xrange(len(queries)):
                random_index = random_batch[index]
                # I assume that this is a wrong method because labels vector will be one-hot-vector
                labels = queries[random_index].get_labels()
                #docIdsLabels = getRankedList(self,queries[random_index],justRel=False) # ids:label pairs collection

                # stochastic training I suppose
                batch_train_loss = self.train_once(X_trains[random_index],queries[random_index],labels)
                batch_train_losses.append(batch_train_loss)


            avg_train_loss = np.mean(batch_train_losses)

            yield {
                'number': epoch,
                'train_loss': avg_train_loss,
            }

