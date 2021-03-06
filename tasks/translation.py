import sys,os
import yaml
import random
import logging
import tensorflow as tf
import pdb
from sklearn.model_selection import train_test_split
import tensorflow.contrib.legacy_seq2seq as seq2seq
from tensorflow.python.platform import gfile

ROOT_PATH = '/'.join(os.path.abspath(__file__).split('/')[:-2])
sys.path.append(ROOT_PATH)
from utils.preprocess import Preprocess
from utils.data_utils import *
from utils.tf_utils import load_pb,write_pb
from embedding import embedding
from encoder import encoder
from common.loss import get_loss
from language_model.bert.modeling import get_assignment_map_from_checkpoint




class Translation(object):
    def __init__(self, conf):
        self.conf = conf
        for attr in conf:
            setattr(self, attr, conf[attr])
        self.task_type = 'seq2seq'

        self.is_training = tf.placeholder(tf.bool, [], name="is_training")
        self.global_step = tf.Variable(0, trainable=False)
        self.keep_prob = tf.where(self.is_training, 0.5, 1.0)

        self.pre = Preprocess()
        self.encode_list, self.decode_list, self.target_list =\
            load_chat_data(conf['train_path'])

        #self.encode_list = [self.pre.get_dl_input_by_text(text) for text in self.encode_list]
        #self.decode_list = [self.pre.get_dl_input_by_text(text) for text in self.decode_list]

        if not self.use_language_model:
            #build vocabulary map using training data
            self.vocab_dict = embedding[self.embedding_type].build_dict(dict_path = self.dict_path, 
                                                                  text_list = self.encode_list)
            self.num_class = len(self.vocab_dict)

            #define embedding object by embedding_type
            self.embedding = embedding[self.embedding_type](text_list = self.encode_list,
                                                            vocab_dict = self.vocab_dict,
                                                            dict_path = self.dict_path,
                                                            random=self.rand_embedding,
                                                            batch_size = self.batch_size,
                                                            maxlen = self.maxlen,
                                                            embedding_size = self.embedding_size,
                                                            conf = self.conf)
            self.embed_encode = self.embedding(name = 'encode_seq')
            self.embed_decode = self.embedding(name = 'decode_seq')

        self.target = tf.placeholder(tf.int32, [None, None], name = 'target_seq')

        #model params
        params = conf
        params.update({
            "maxlen":self.maxlen,
            "embedding_size":self.embedding_size,
            "keep_prob":self.keep_prob,
            "batch_size": self.batch_size,
            "num_output": self.num_class,
            "is_training": self.is_training
        })
        self.encoder = encoder[self.encoder_type](**params)

        self.output_nodes = [] 
        if not self.use_language_model:
            self.out, self.final_state_encode, self.final_state_decode, pb_nodes  = self.encoder(self.embed_encode, self.embed_decode)
            for item in pb_nodes:
                self.output_nodes.append(item)
            self.output_nodes.append(self.final_state_decode.name.split(':')[0])
            self.output_nodes.append(self.final_state_encode.name.split(':')[0])
            self.output_nodes.append(self.out.name.split(':')[0])
        else:
            self.out = self.encoder()
            self.output_nodes.append(self.out.name.split(':')[0])

        self.loss(self.out)

        self.sess = tf.Session()
        self.sess.run(tf.global_variables_initializer())
        self.saver = tf.train.Saver(tf.global_variables())
        if self.use_language_model:
            tvars = tf.trainable_variables()
            init_checkpoint = conf['init_checkpoint_path']
            (assignment_map, initialized_variable_names) = \
                get_assignment_map_from_checkpoint(tvars, init_checkpoint)
            tf.train.init_from_checkpoint(init_checkpoint,assignment_map)

    def loss(self, out):
        with tf.name_scope("loss"):
            targets = tf.reshape(self.target, [-1])
            out = tf.reshape(out, [-1, self.num_class])
            loss = seq2seq.sequence_loss_by_example([out],
                                                [targets],
                                                [tf.ones_like(targets, dtype=tf.float32)])
            self.loss = tf.reduce_mean(loss)
            self.optimizer = tf.train.AdamOptimizer(self.learning_rate).minimize(
                self.loss, global_step=self.global_step)

        with tf.name_scope("output"):
            out = tf.nn.softmax(out)
            self.prob = tf.reshape(out,[-1, self.maxlen, self.num_class], name = 'prob')
            out_max = tf.argmax(self.prob,-1, output_type = tf.int32)
            self.predictions = tf.reshape(out_max, [-1, self.maxlen], name = 'predictions')

        with tf.name_scope("accuracy"):
            correct_predictions = tf.equal(self.predictions, self.target)
            self.accuracy = tf.reduce_mean(tf.cast(correct_predictions, "float"), name="accuracy")

    def train(self):
        logging.info("---------start train---------")
        self.train_data = zip(self.encode_list, self.decode_list, self.target_list)
        train_batches = batch_iter(self.train_data, self.batch_size, self.num_epochs)
        num_batches_per_epoch = (len(self.encode_list) - 1) // self.batch_size + 1
        max_accuracy = -1
        for batch in train_batches:
            encode_batch, decode_batch, target_batch = zip(*batch)

            train_feed_dict = {
                self.is_training: True
            }
            if not self.use_language_model:
                _, encode_batch, len_encode_batch = self.embedding.text2id(
                    encode_batch, self.vocab_dict, self.maxlen, need_preprocess = False)
                _, decode_batch, len_decode_batch = self.embedding.text2id(
                    decode_batch, self.vocab_dict, self.maxlen, need_preprocess = False)
                _, target_batch, len_target_batch = self.embedding.text2id(
                    target_batch, self.vocab_dict, self.maxlen, need_preprocess = False)

                train_feed_dict.update(self.embedding.feed_dict(encode_batch,'encode_seq'))
                train_feed_dict.update(self.embedding.feed_dict(decode_batch,'decode_seq'))
                train_feed_dict.update({self.target:target_batch})
                train_feed_dict.update(self.encoder.feed_dict(len = (len_encode_batch,len_decode_batch)))
            else:
                train_feed_dict.update(self.encoder.feed_dict(x_batch))
            _, step, loss, predictions = self.sess.run([self.optimizer, self.global_step,
                                           self.loss, self.predictions], feed_dict=train_feed_dict)
            #vocab_dict_rev = {self.vocab_dict[key]:key for key in self.vocab_dict}
            #predict_word = [vocab_dict_rev[idx] for idx in predictions[0]]

            if step % (self.valid_step/10) == 0:
                logging.info("step {0}: loss = {1}".format(step, loss))
            if step % self.valid_step == 0:
                # Test accuracy with validation data for each epoch.
                self.saver.save(self.sess,
                                "{0}/{1}.ckpt".format(self.checkpoint_path,
                                                          self.task_type),
                                global_step=step)
                self.save_pb()
                logging.info("Model is saved.\n")

    def save_pb(self):
        node_list = ['is_training','output/predictions', 'accuracy/accuracy']
        node_list += self.output_nodes
        write_pb(self.checkpoint_path, self.model_path, node_list)

    def test_unit(self, text):
        if not os.path.exists(self.model_path):
            self.save_pb()
        graph = load_pb(self.model_path)
        sess = tf.Session(graph=graph)


        self.target = graph.get_operation_by_name("target_seq").outputs[0]
        self.is_training = graph.get_operation_by_name("is_training").outputs[0]

        #self.state = graph.get_tensor_by_name(self.output_nodes[-2]+":0")
        self.final_state_decode = graph.get_tensor_by_name(self.output_nodes[-3]+":0")
        self.final_state_encode = graph.get_tensor_by_name(self.output_nodes[-2]+":0")
        self.prob = graph.get_tensor_by_name("output/prob:0")
        self.predictions = graph.get_tensor_by_name("output/predictions:0")

        vocab_dict = embedding[self.embedding_type].build_dict(self.dict_path,mode = 'test')
        vocab_dict_rev = {vocab_dict[key]:key for key in vocab_dict}


        feed_dict = {
            self.is_training: False
        }
        preprocess_x, encode_batch, len_batch = self.embedding.text2id([text], 
                                                                       vocab_dict,
                                                                       self.maxlen)
        feed_dict.update(self.embedding.pb_feed_dict(graph, encode_batch, 'encode_seq'))
        feed_dict.update(self.encoder.pb_feed_dict(graph, len = (len_batch,None)))
        state = sess.run(self.final_state_encode, feed_dict=feed_dict)
        pdb.set_trace()
        state = state.tolist()
        word = '<s>'
        len_idx = 0
        while word != '。':
            len_idx +=1
            if len_idx > 80: break
            if word == '</s>': break
            text += word
            word, state = self.run(sess, graph, vocab_dict, vocab_dict_rev, 
                                   state, word)
        text += word
        logging.info(text)


    def choose_word(self, prob, vocab_dict_rev):
        return vocab_dict_rev[np.argmax(prob)]

    def run(self, sess, graph, vocab_dict, vocab_dict_rev, state, inputs):
        feed_dict = {
            self.is_training: False
        }
        preprocess_x, batch_x, len_batch = self.embedding.text2id([inputs],
                                                                  vocab_dict,
                                                                  self.maxlen)
        #feed_dict.update(self.embedding.pb_feed_dict(graph, batch_x, 'encode_seq'))
        feed_dict.update(self.embedding.pb_feed_dict(graph, batch_x, 'decode_seq'))
        #feed_dict.update(self.encoder.pb_feed_dict(graph, len = (len_batch, len_batch),
        feed_dict.update(self.encoder.pb_feed_dict(graph, len = (None, len_batch),
                                                   initial_state = state))

        #prob, state = sess.run([self.prob, self.state], feed_dict=feed_dict)
        #word = self.choose_word(prob[0][-1], vocab_dict_rev)
        final_id = len_batch[0] - 1
        predictions, state = sess.run([self.predictions, self.final_state_decode], feed_dict=feed_dict)
        word = vocab_dict_rev[predictions[0][final_id]]

        return word, state.tolist()

