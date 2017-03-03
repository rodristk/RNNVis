"""
The backend manager for handling the models
"""

import hashlib
import os

from datasets.data_utils import Feeder, SentenceProducer
from rnnvis.utils.io_utils import get_path, assert_path_exists
from rnnvis.procedures import build_model, pour_data
# from rnnvis.db import language_model
from rnnvis.rnn.eval_recorder import BufferRecorder, StateRecorder
from rnnvis.state_processor import get_state_signature, get_empirical_strength, strength2json, \
    get_tsne_projection, solution2json

_config_dir = 'config'
_data_dir = 'cached_data'
_model_dir = 'models'


class ModelManager(object):

    _available_models = {
        'PTB-LSTM': {'config': 'lstm.yml'},
        'Shakespeare': {'config': 'shakespeare.yml'},
        'IMDB': {'config': 'imdb-tiny.yml'},
        'PTB-GRU': {'config': 'gru.yml'}
    }

    def __init__(self):
        self._models = {}
        self._train_configs = {}
        # self._records = {}
        # self._data = {}

    @property
    def available_models(self):
        return list(self._available_models.keys())

    def get_config_filename(self, name):
        """
        Get the config file path of a given model
        :param name: the name of the model, should be in _available_models
        :return: file path if the name is in _available_models, else None
        """
        if name in self._available_models:
            return get_path(_config_dir, self._available_models[name]['config'])
        else:
            return None

    def _get_model(self, name, train=False):
        if name in self._models:
            return self._models[name]
        else:
            flag = self._load_model(name, train)
            return self._models[name] if flag else None

    def _load_model(self, name, train=False):
        if name in self._available_models:
            config_file = get_path(_config_dir, self._available_models[name]['config'])
            model, train_config = build_model(config_file)
            # data_name = self._available_models[name]['data']
            # data = language_model.get_datasets_by_name(data_name)
            # if data is None:
            #     if name == 'PTB':
            #         language_model.store_ptb(get_path('cached_data/simple-examples/data'), data_name)
            #     elif name == 'Shakespeare':
            #         language_model.store_plain_text(get_path('cached_data/tinyshakespeare.txt', data_name),
            #                                         'shakespeare', {'train': 0.9, 'valid': 0.05, 'test': 0.05})
            #     data = language_model.get_datasets_by_name(self._available_models[name]['data'])
            model.add_generator()
            model.add_evaluator(1, 1, 100, True)
            if not train:
                # If not training, the model should already be trained
                assert_path_exists(get_path(_model_dir, model.name))
                model.restore()
            self._models[name] = model
            self._train_configs[name] = train_config
            return True
        else:
            print('WARN: Cannot find model with name {:s}'.format(name))
            return False

    def model_generate(self, name, seeds, max_branch=1, accum_cond_prob=0.9,
                       min_cond_prob=0.0, min_prob=0.0, max_step=10, neg_word_ids=None):
        """
        :param name: name of the model
        :param seeds: a list of word_id or a list of words
        :param max_branch: the maximum number of branches at each node
        :param accum_cond_prob: the maximum accumulate conditional probability of the following branches
        :param min_cond_prob: the minimum conditional probability of each branch
        :param min_prob: the minimum probability of a branch (note that this indicates a multiplication along the tree)
        :param max_step: the step to generate
        :param neg_word_ids: a set of neglected words' ids.
        :return:
        """
        model = self._get_model(name)
        if model is None:
            return None
        return model.generate(seeds, None, max_branch, accum_cond_prob, min_cond_prob, min_prob, max_step, neg_word_ids)

    def model_record_sequence(self, name, sequences):
        model = self._get_model(name)
        if model is None:
            return None
        config = self._train_configs[name]
        max_len = max([len(s) for s in sequences])
        recorder = BufferRecorder(config.dataset, name, 500)
        if not isinstance(sequences, Feeder):
            producer = SentenceProducer(sequences, 1, max_len, num_steps=1)
            sequences = producer.get_feeder()
        model.evaluator.record_every = max_len
        try:
            model.evaluate_and_record(sequences, None, recorder, verbose=False)
            return recorder.evals()  # a sequence of eval_doc, records pairs
        except ValueError:
            print("ERROR: Fail to evaluate given sequence! Sequence length too large!")
            return None
        except:
            print("ERROR: Fail to evaluate given sequence! Unknown Reason.")
            return None

    def model_record_default(self, name, dataset):
        """
        record default datasets
        :param name: model name
        :param dataset: 'train', 'valid', 'test'
        :return: True or False, None if model not exists
        """
        model = self._get_model(name)
        if model is None:
            return None
        config = self._train_configs[name]
        assert dataset in ['test', 'train', 'valid'], "dataset should be 'train', 'valid' or 'test'"
        recorder = StateRecorder(config.dataset, model.name, 500)
        producers = pour_data(config.dataset, [dataset], 1, 1, config.num_steps)
        inputs, targets, epoch_size = producers[0]
        model.evaluator.record_every = config.num_steps
        try:
            model.evaluate_and_record(inputs, None, recorder, verbose=False)
            return True
        except:
            print("ERROR: Fail to evaluate given sequence!")
            return False

    def model_sentences_to_ids(self, name, sentences):
        model = self._get_model(name)
        if model is None:
            return None
        ids = [model.get_id_from_word(sentence) for sentence in sentences]
        return ids

    def model_state_signature(self, name, state_name, layers, sample_size=1000):
        model = self._get_model(name)
        if model is None:
            return None
        config = self._train_configs[name]
        model_name = model.name
        data_name = config.dataset
        return get_state_signature(data_name, model_name, state_name, layers, sample_size, dim=None).tolist()

    def model_strength(self, name, state_name, layers, top_k=100):
        model = self._get_model(name)
        if model is None:
            return None
        config = self._train_configs[name]
        strength_mat = get_empirical_strength(config.dataset, model.name, state_name, layers, top_k)
        id_to_word = model.id_to_word
        word_list = id_to_word[:top_k]
        return strength2json(strength_mat, word_list)

    def model_state_projection(self, name, state_name, layer=-1, method='tsne'):
        model = self._get_model(name)
        if model is None:
            return None
        config = self._train_configs[name]
        layer_num = len(model.cell_list)
        if method == 'tsne':
            tsne_solution = get_tsne_projection(config.dataset, model.name, state_name, layer, 5000, 50, 40.0)
            labels = [layer_num - 1 if layer < 0 else layer] * tsne_solution.shape[0]
            states_num = [0] * layer_num
            states_num[layer] = tsne_solution.shape[0]
            return solution2json(tsne_solution, states_num, labels)
        else:
            return None


def hash_tag_str(text_list):
    """Use hashlib.md5 to tag a hash str of a list of text"""
    return hashlib.md5(" ".join(text_list).encode()).hexdigest()

if __name__ == '__main__':
    print(os.path.realpath(__file__))
