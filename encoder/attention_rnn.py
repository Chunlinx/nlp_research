import tensorflow as tf
from tensorflow.contrib import rnn
import numpy as np
import pdb
from common.layers import RNNLayer
from encoder_base import Base
import copy

class AttentionRNN(object):
    def __init__(self, **kwargs):
        super(AttentionRNN, self).__init__(**kwargs)
        self.maxlen = kwargs['maxlen']
        self.num_hidden = 256
        self.num_layers = 2
        self.keep_prob = kwargs['keep_prob']
        self.batch_size = kwargs['batch_size']
        self.rnn_type = kwargs['rnn_type']
        self.num_output = kwargs['num_output']
        self.rnn_layer = RNNLayer(self.rnn_type, 
                                  self.num_hidden,
                                  self.num_layers)
        self.placeholder = {}

    def __call__(self, embed, name = 'encoder', features = None, 
                 reuse = tf.AUTO_REUSE, **kwargs):
        length_name = name + "_length" 
        self.placeholder[length_name] = tf.placeholder(dtype=tf.int32, 
                                                shape=[None], 
                                                name = length_name)
        if features != None:
            self.features = copy.copy(self.placeholder)
            self.placeholder[length_name] = features[length_name]

        with tf.variable_scope("attention_rnn", reuse = reuse):
            pdb.set_trace()
            outputs, _, state = self.rnn_layer(inputs = embed,
                              seq_len = self.placeholder[length_name])
            with tf.variable_scope("attention", reuse = reuse):
                attention_score = tf.nn.softmax(tf.layers.dense(outputs, 1, activation=tf.nn.tanh), axis=1)
                attention_out = tf.squeeze(
                    tf.matmul(tf.transpose(outputs, perm=[0, 2, 1]), attention_score),
                    axis=-1)
                h_drop = tf.nn.dropout(attention_out, self.keep_prob)
                dense = tf.layers.dense(h_drop, self.num_output, activation=None)
                return dense

    def feed_dict(self, name = 'encoder', **kwargs):
        feed_dict = {}
        for key in kwargs:
            length_name = key + "_length" 
            feed_dict[self.placeholder[length_name]] = kwargs[key]

        return feed_dict

    def pb_feed_dict(self, graph, name = 'encoder', **kwargs):
        feed_dict = {}
        for key in kwargs:
            length_name = key + "_length" 
            key_node = graph.get_operation_by_name(length_name).outputs[0]
            feed_dict[key_node] = kwargs[key]
        return feed_dict

