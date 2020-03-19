"""This module implements the object model for EMMAA model testing."""
import json
import pickle
import logging
import itertools
import jsonpickle
import os
from fnvhash import fnv1a_32
from urllib import parse
from indra.explanation.model_checker import PysbModelChecker, \
    PybelModelChecker, SignedGraphModelChecker, UnsignedGraphModelChecker
from indra.explanation.reporting import stmts_from_pysb_path, \
    stmts_from_pybel_path, stmts_from_indranet_path, PybelEdge, \
    pybel_edge_to_english
from indra.assemblers.english.assembler import EnglishAssembler
from indra.sources.indra_db_rest.api import get_statement_queries
from indra.statements import Statement, Agent, Concept, Event
from indra.util.statement_presentation import group_and_sort_statements
from bioagents.tra.tra import TRA, MissingMonomerError, MissingMonomerSiteError
from emmaa.model import EmmaaModel
from emmaa.queries import PathProperty, DynamicProperty
from emmaa.util import make_date_str, get_s3_client, get_class_from_name, \
    EMMAA_BUCKET_NAME, find_latest_s3_file


logger = logging.getLogger(__name__)


result_codes_link = 'https://emmaa.readthedocs.io/en/latest/dashboard/response_codes.html'
elsevier_url = 'https://www.sciencedirect.com/science/article/pii/'
RESULT_CODES = {
    'STATEMENT_TYPE_NOT_HANDLED': 'Statement type not handled',
    'SUBJECT_MONOMERS_NOT_FOUND': 'Statement subject not in model',
    'SUBJECT_NOT_FOUND': 'Statement subject not in model',
    'OBSERVABLES_NOT_FOUND': 'Statement object state not in model',
    'OBJECT_NOT_FOUND': 'Statement object state not in model',
    'NO_PATHS_FOUND': 'No path found that satisfies the test statement',
    'MAX_PATH_LENGTH_EXCEEDED': 'Path found but exceeds search depth',
    'PATHS_FOUND': 'Path found which satisfies the test statement',
    'INPUT_RULES_NOT_FOUND': 'No rules with test statement subject',
    'MAX_PATHS_ZERO': 'Path found but not reconstructed',
    'QUERY_NOT_APPLICABLE': 'Query is not applicable for this model'
}
ARROW_DICT = {'Complex': u"\u2194",
              'Inhibition': u"\u22A3",
              'DecreaseAmount': u"\u22A3"}


class ModelManager(object):
    """Manager to generate and store properties of a model and relevant tests.

    Parameters
    ----------
    model : emmaa.model.EmmaaModel
        EMMAA model

    Attributes
    ----------
    mc_mapping : dict
        A dictionary mapping a ModelChecker type to a corresponding method
        for assembling the model and a ModelChecker class.
    mc_types : dict
        A dictionary in which each key is a type of a ModelChecker and value is
        a dictionary containing an instance of a model, an instance of a
        ModelChecker and a list of test results.
    entities : list[indra.statements.agent.Agent]
        A list of entities of EMMAA model.
    applicable_tests : list[emmaa.model_tests.EmmaaTest]
        A list of EMMAA tests applicable for given EMMAA model.
    make_links : bool
        Whether to include links to INDRA db in test results.
    link_type : str
        A name of a source to link the statements to (e.g. 'indra_db' or
        'elsevier')
    date_str : str
        Time when this object was created.
    """
    def __init__(self, model):
        self.model = model
        self.mc_mapping = {
            'pysb': (self.model.assemble_pysb, PysbModelChecker,
                     stmts_from_pysb_path),
            'pybel': (self.model.assemble_pybel, PybelModelChecker,
                      stmts_from_pybel_path),
            'signed_graph': (self.model.assemble_signed_graph,
                             SignedGraphModelChecker,
                             stmts_from_indranet_path),
            'unsigned_graph': (self.model.assemble_unsigned_graph,
                               UnsignedGraphModelChecker,
                               stmts_from_indranet_path)}
        self.mc_types = {}
        for mc_type in model.test_config.get('mc_types', ['pysb']):
            self.mc_types[mc_type] = {}
            assembled_model = self.mc_mapping[mc_type][0]()
            self.mc_types[mc_type]['model'] = assembled_model
            self.mc_types[mc_type]['model_checker'] = (
                self.mc_mapping[mc_type][1](assembled_model))
            self.mc_types[mc_type]['test_results'] = []
        self.entities = self.model.get_assembled_entities()
        self.applicable_tests = []
        self.make_links = model.test_config.get('make_links', True)
        self.link_type = model.test_config.get('link_type', 'indra_db')
        self.date_str = make_date_str()

    def get_updated_mc(self, mc_type, stmts):
        """Update the ModelChecker and graph with stmts for tests/queries."""
        mc = self.mc_types[mc_type]['model_checker']
        mc.statements = stmts
        if mc_type == 'pysb':
            mc.graph = None
            mc.get_graph(prune_im=True, prune_im_degrade=True)
        return mc

    def add_test(self, test):
        """Add a test to a list of applicable tests."""
        self.applicable_tests.append(test)

    def add_result(self, mc_type, result):
        """Add a result to a list of results."""
        self.mc_types[mc_type]['test_results'].append(result)

    def run_all_tests(self):
        """Run all applicable tests with all available ModelCheckers."""
        max_path_length, max_paths = self._get_test_configs()
        for mc_type in self.mc_types:
            self.run_tests_per_mc(mc_type, max_path_length, max_paths)

    def run_tests_per_mc(self, mc_type, max_path_length, max_paths):
        """Run all applicable tests with one ModelChecker."""
        mc = self.get_updated_mc(
            mc_type, [test.stmt for test in self.applicable_tests])
        logger.info(f'Running the tests with {mc_type} ModelChecker.')
        results = mc.check_model(
            max_path_length=max_path_length, max_paths=max_paths)
        for (stmt, result) in results:
            self.add_result(mc_type, result)

    def make_path_json(self, mc_type, result):
        paths = []
        if result.paths:
            for path in result.paths:
                path_nodes = []
                edge_list = []
                report_function = self.mc_mapping[mc_type][2]
                model = self.mc_types[mc_type]['model']
                stmts = self.model.assembled_stmts
                if mc_type == 'pysb':
                    report_stmts = report_function(path, model, stmts)
                    path_stmts = [[st] for st in report_stmts]
                    merge = False
                elif mc_type == 'pybel':
                    path_stmts = report_function(path, model, False, stmts)
                    merge = False
                elif mc_type == 'signed_graph':
                    path_stmts = report_function(path, model, True, False, stmts)
                    merge = True
                elif mc_type == 'unsigned_graph':
                    path_stmts = report_function(path, model, False, False, stmts)
                    merge = True
                for i, step in enumerate(path_stmts):
                    edge_nodes = []
                    if len(step) < 1:
                        continue
                    stmt_type = type(step[0]).__name__
                    if stmt_type == 'PybelEdge':
                        source, target = step[0].source, step[0].target
                        edge_nodes.append(source.name)
                        edge_nodes.append(u"\u2192")
                        edge_nodes.append(target.name)
                    else:
                        for j, ag in enumerate(step[0].agent_list()):
                            if ag is not None:
                                edge_nodes.append(ag.name)
                            if j == (len(step[0].agent_list()) - 1):
                                break
                            if stmt_type in ARROW_DICT:
                                edge_nodes.append(ARROW_DICT[stmt_type])
                            else:
                                edge_nodes.append(u"\u2192")
                    if i == 0:
                        for n in edge_nodes:
                            path_nodes.append(n)
                    else:
                        for n in edge_nodes[1:]:
                            path_nodes.append(n)
                    step_sentences = self._make_path_stmts(step, merge=merge)
                    edge_dict = {'edge': ' '.join(edge_nodes),
                                 'stmts': step_sentences}
                    edge_list.append(edge_dict)
                path_json = {'path': ' '.join(path_nodes),
                             'edge_list': edge_list}
                paths.append(path_json)
        return paths

    def _make_path_stmts(self, stmts, merge=False):
        sentences = []
        if merge:
            groups = group_and_sort_statements(stmts)
            for group in groups:
                group_stmts = group[-1]
                stmt_type = group[0][-1]
                agent_names = group[0][1]
                if len(agent_names) < 2:
                    continue
                if stmt_type == 'Influence':
                    stmt = get_class_from_name(stmt_type, Statement)(
                        Event(Concept(agent_names[0])),
                        Event(Concept(agent_names[1])))
                else:
                    try:
                        stmt = get_class_from_name(stmt_type, Statement)(
                            Agent(agent_names[0]), Agent(agent_names[1]))
                    except ValueError:
                        stmt = get_class_from_name(stmt_type, Statement)(
                            [Agent(ag_name) for ag_name in agent_names])
                ea = EnglishAssembler([stmt])
                sentence = ea.make_model()
                stmt_hashes = [gr_st.get_hash() for gr_st in group_stmts]
                url_param = parse.urlencode(
                    {'stmt_hash': stmt_hashes, 'source': 'model_statement',
                     'model': self.model.name}, doseq=True)
                link = f'/evidence/?{url_param}'
                sentences.append((link, sentence, ''))
        else:
            for stmt in stmts:
                if isinstance(stmt, PybelEdge):
                    sentence = pybel_edge_to_english(stmt)
                    sentences.append(('', sentence, ''))
                else:
                    ea = EnglishAssembler([stmt])
                    sentence = ea.make_model()
                    stmt_hashes = [stmt.get_hash()]
                    url_param = parse.urlencode(
                        {'stmt_hash': stmt_hashes, 'source': 'model_statement',
                         'model': self.model.name}, doseq=True)
                    link = f'/evidence/?{url_param}'
                    sentences.append((link, sentence, ''))
        return sentences

    def make_result_code(self, result):
        result_code = result.result_code
        return RESULT_CODES[result_code]

    def answer_query(self, query, **kwargs):
        if isinstance(query, DynamicProperty):
            return self.answer_dynamic_query(query, **kwargs)
        if isinstance(query, PathProperty):
            return self.answer_path_query(query)

    def answer_path_query(self, query):
        """Answer user query with a path if it is found."""
        if ScopeTestConnector.applicable(self, query):
            results = []
            for mc_type in self.mc_types:
                mc = self.get_updated_mc(mc_type, [query.path_stmt])
                max_path_length, max_paths = self._get_test_configs(
                    mode='query', mc_type=mc_type, default_paths=5)
                result = mc.check_statement(
                    query.path_stmt, max_paths, max_path_length)
                results.append((mc_type, self.process_response(mc_type, result)))
            return results
        else:
            return [('', self.hash_response_list(
                RESULT_CODES['QUERY_NOT_APPLICABLE']))]

    def answer_dynamic_query(self, query, use_kappa=False,
                             bucket=EMMAA_BUCKET_NAME):
        """Answer user query by simulating a PySB model."""
        tra = TRA(use_kappa=use_kappa)
        tp = query.get_temporal_pattern()
        try:
            sat_rate, num_sim, kpat, pat_obj, fig_path = tra.check_property(
                self.mc_types['pysb']['model'], tp)
            fig_name, ext = os.path.splitext(os.path.basename(fig_path))
            date_str = make_date_str()
            s3_key = f'query_images/{self.model.name}/{fig_name}_{date_str}{ext}'
            s3_path = f'https://{bucket}.s3.amazonaws.com/{s3_key}'
            client = get_s3_client(unsigned=False)
            logger.info(f'Uploading image to {s3_path}')
            client.upload_file(fig_path, Bucket=bucket, Key=s3_key)
            resp_json = {'sat_rate': sat_rate, 'num_sim': num_sim,
                         'kpat': kpat, 'fig_path': s3_path}
        except (MissingMonomerError, MissingMonomerSiteError):
            resp_json = RESULT_CODES['QUERY_NOT_APPLICABLE']
        return [('pysb', self.hash_response_list(resp_json))]

    def answer_queries(self, queries, **kwargs):
        """Answer all queries registered for this model.

        Parameters
        ----------
        queries : list[emmaa.queries.Query]
            A list of queries to run.

        Returns
        -------
        responses : list[tuple(json, json)]
            A list of tuples each containing a query, mc_type and result json.
        """
        responses = []
        applicable_queries = []
        applicable_stmts = []
        for query in queries:
            if isinstance(query, DynamicProperty):
                mc_type, response = self.answer_dynamic_query(
                    query, **kwargs)[0]
                responses.append((query, mc_type, response))
            elif isinstance(query, PathProperty):
                if ScopeTestConnector.applicable(self, query):
                    applicable_queries.append(query)
                    applicable_stmts.append(query.path_stmt)
                else:
                    responses.append(
                        (query, '', self.hash_response_list(
                            RESULT_CODES['QUERY_NOT_APPLICABLE'])))
        # Only do the following steps if there are applicable queries
        if applicable_queries:
            for mc_type in self.mc_types:
                mc = self.get_updated_mc(mc_type, applicable_stmts)
                max_path_length, max_paths = self._get_test_configs(
                    mode='query', mc_type=mc_type, default_paths=5)
                results = mc.check_model(
                    max_path_length=max_path_length, max_paths=max_paths)
                for ix, (_, result) in enumerate(results):
                    responses.append(
                        (applicable_queries[ix], mc_type,
                         self.process_response(mc_type, result)))
        return sorted(responses, key=lambda x: x[0].matches_key())

    def _get_test_configs(self, mode='test', mc_type=None, default_length=5,
                          default_paths=1):
        if mode == 'test':
            config = self.model.test_config
        elif mode == 'query':
            config = self.model.query_config
        try:
            max_path_length = \
                config['statement_checking'][mc_type]['max_path_length']
        except KeyError:
            try:
                max_path_length = \
                    config['statement_checking']['max_path_length']
            except KeyError:
                max_path_length = default_length
        try:
            max_paths = \
                config['statement_checking'][mc_type]['max_paths']
        except KeyError:
            try:
                max_paths = \
                    config['statement_checking']['max_paths']
            except KeyError:
                max_paths = default_paths
        logger.info('Parameters for model checking: %d, %d' %
                    (max_path_length, max_paths))
        return (max_path_length, max_paths)

    def process_response(self, mc_type, result):
        """Return a dictionary in which every key is a hash and value is a list
        of tuples. Each tuple contains a sentence describing either a step in a
        path (if it was found) or result code (if a path was not found) and a
        link leading to a webpage with more information about corresponding
        sentence.
        """
        if result.paths:
            response = self.make_path_json(mc_type, result)
        else:
            response = self.make_result_code(result)
        return self.hash_response_list(response)

    def hash_response_list(self, response):
        """Return a dictionary mapping a hash with a response in a response
        list.
        """
        response_dict = {}
        if isinstance(response, str):
            response_hash = str(fnv1a_32(response.encode('utf-8')))
            response_dict[response_hash] = response
        elif isinstance(response, list):
            for resp in response:
                sentences = []
                for edge in resp['edge_list']:
                    for (_, sentence, _) in edge['stmts']:
                        sentences.append(sentence)
                response_str = ' '.join(sentences)
                response_hash = str(fnv1a_32(response_str.encode('utf-8')))
                response_dict[response_hash] = resp
        elif isinstance(response, dict):
            results = [str(response.get('sat_rate')),
                       str(response.get('num_sim'))]
            response_str = ' '.join(results)
            response_hash = str(fnv1a_32(response_str.encode('utf-8')))
            response_dict[response_hash] = response
        else:
            raise TypeError('Response should be a string or a list.')
        return response_dict

    def assembled_stmts_to_json(self):
        """Put assembled statements to JSON format."""
        stmts = []
        for stmt in self.model.assembled_stmts:
            stmts.append(stmt.to_json())
        return stmts

    def results_to_json(self):
        """Put test results to json format."""
        pickler = jsonpickle.pickler.Pickler()
        results_json = []
        results_json.append({
            'model_name': self.model.name,
            'mc_types': [mc_type for mc_type in self.mc_types.keys()],
            'link_type': self.link_type})
        for ix, test in enumerate(self.applicable_tests):
            test_ix_results = {'test_type': test.__class__.__name__,
                               'test_json': test.to_json()}
            for mc_type in self.mc_types:
                result = self.mc_types[mc_type]['test_results'][ix]
                test_ix_results[mc_type] = {
                    'result_json': pickler.flatten(result),
                    'path_json': self.make_path_json(mc_type, result),
                    'result_code': self.make_result_code(result)}
            results_json.append(test_ix_results)
        return results_json

    def upload_results(self, test_corpus='large_corpus_tests',
                       bucket=EMMAA_BUCKET_NAME):
        """Upload results to s3 bucket."""
        json_dict = self.results_to_json()
        result_key = (f'results/{self.model.name}/results_'
                      f'{test_corpus}_{self.date_str}.json')
        json_str = json.dumps(json_dict, indent=1)
        client = get_s3_client(unsigned=False)
        logger.info(f'Uploading test results to {result_key}')
        client.put_object(Bucket=bucket, Key=result_key,
                          Body=json_str.encode('utf8'))


class TestManager(object):
    """Manager to generate and run a set of tests on a set of models.

    Parameters
    ----------
    model_managers : list[emmaa.model_tests.ModelManager]
        A list of ModelManager objects
    tests : list[emmaa.model_tests.EmmaaTest]
        A list of EMMAA tests
    """
    def __init__(self, model_managers, tests):
        self.model_managers = model_managers
        self.tests = tests

    def make_tests(self, test_connector):
        """Generate a list of applicable tests for each model with a given test
        connector.

        Parameters
        ----------
        test_connector : emmaa.model_tests.TestConnector
            A TestConnector object to use for connecting models to tests.
        """
        logger.info(f'Checking applicability of {len(self.tests)} tests to '
                    f'{len(self.model_managers)} models')
        for model_manager, test in itertools.product(self.model_managers,
                                                     self.tests):
            if test_connector.applicable(model_manager, test):
                model_manager.add_test(test)
                logger.debug(f'Test {test.stmt} is applicable')
            else:
                logger.debug(f'Test {test.stmt} is not applicable')
        logger.info(f'Created tests for {len(self.model_managers)} models.')
        for model_manager in self.model_managers:
            logger.info(f'Created {len(model_manager.applicable_tests)} tests '
                        f'for {model_manager.model.name} model.')

    def run_tests(self):
        """Run tests for a list of model-test pairs"""
        for model_manager in self.model_managers:
            model_manager.run_all_tests()


class TestConnector(object):
    """Determines if a given test is applicable to a given model."""
    def __init__(self):
        pass

    @staticmethod
    def applicable(model, test):
        """Return True if the test is applicable to the given model."""
        return True


class ScopeTestConnector(TestConnector):
    """Determines applicability of a test to a model by overlap in scope."""
    @staticmethod
    def applicable(model, test):
        """Return True of all test entities are in the set of model entities"""
        model_entities = model.entities
        test_entities = test.get_entities()
        # TODO
        # After adding entities as an attribute to StatementCheckingTest(), use
        # test_entities = test.entities
        return ScopeTestConnector._overlap(model_entities, test_entities)

    @staticmethod
    def _overlap(model_entities, test_entities):
        me_names = {e.name for e in model_entities}
        te_names = {e.name for e in test_entities}
        # If all test entities are in model entities, we get an empty set here
        # so we return True
        return not te_names - me_names


class EmmaaTest(object):
    """Represent an EMMAA test condition"""
    def get_entities(self):
        """Return a list of entities that the test checks for."""
        raise NotImplementedError()


class StatementCheckingTest(EmmaaTest):
    """Represent an EMMAA test condition that checks a PySB-assembled model
    against an INDRA Statement."""
    def __init__(self, stmt, configs=None):
        self.stmt = stmt
        self.configs = {} if not configs else configs
        logger.info('Test configs: %s' % configs)
        # TODO
        # Add entities as a property if we can reload tests on s3.
        # self.entities = self.get_entities()

    def check(self, model_checker, pysb_model):
        """Use a model checker to check if a given model satisfies the test."""
        max_path_length = self.configs.get('max_path_length', 5)
        max_paths = self.configs.get('max_paths', 1)
        logger.info('Parameters for model checking: %s, %d' %
                    (max_path_length, max_paths))
        res = model_checker.check_statement(
            self.stmt,
            max_path_length=max_path_length,
            max_paths=max_paths)
        return res

    def get_entities(self):
        """Return a list of entities that the test checks for."""
        return self.stmt.agent_list()

    def to_json(self):
        return self.stmt.to_json()

    def __repr__(self):
        return "%s(stmt=%s)" % (self.__class__.__name__, repr(self.stmt))


def load_tests_from_s3(test_name, bucket=EMMAA_BUCKET_NAME):
    """Load Emmaa Tests with the given name from S3.

    Parameters
    ----------
    test_name : str
        Looks for a test file in the emmaa bucket on S3 with key
        'tests/{test_name}'.

    Return
    ------
    list of EmmaaTest
        List of EmmaaTest objects loaded from S3.
    """
    client = get_s3_client()
    prefix = f'tests/{test_name}'
    try:
        test_key = find_latest_s3_file(bucket, prefix)
    except ValueError:
        test_key = f'tests/{test_name}.pkl'
    logger.info(f'Loading tests from {test_key}')
    obj = client.get_object(Bucket=bucket, Key=test_key)
    tests = pickle.loads(obj['Body'].read())
    return tests, test_key


def save_model_manager_to_s3(model_name, model_manager,
                             bucket=EMMAA_BUCKET_NAME):
    client = get_s3_client(unsigned=False)
    logger.info(f'Saving a model manager for {model_name} model to S3.')
    date_str = model_manager.date_str
    client.put_object(
        Body=pickle.dumps(model_manager), Bucket=bucket,
        Key=f'results/{model_name}/model_manager_{date_str}.pkl')


def load_model_manager_from_s3(model_name=None, key=None,
                               bucket=EMMAA_BUCKET_NAME):
    client = get_s3_client()
    # First try find the file from specified key
    if key:
        try:
            obj = client.get_object(Bucket=bucket, Key=key)
            body = obj['Body'].read()
            model_manager = pickle.loads(body)
            return model_manager
        except Exception:
            logger.info(f'No file found with key {key}')
    # Now try find the latest key for given model
    if model_name:
        # Versioned
        key = find_latest_s3_file(
            bucket, f'results/{model_name}/model_manager_', '.pkl')
        if key is None:
            # Non-versioned
            key = f'results/{model_name}/latest_model_manager.pkl'
        return load_model_manager_from_s3(key=key, bucket=bucket)
    # Could not find either from key or from model name.
    logger.info('Could not find the model manager.')
    return None


def update_model_manager_on_s3(model_name, bucket=EMMAA_BUCKET_NAME):
    model = EmmaaModel.load_from_s3(model_name, bucket=bucket)
    mm = ModelManager(model)
    save_model_manager_to_s3(model_name, mm, bucket=bucket)
    return mm


def model_to_tests(model_name, upload=True, bucket=EMMAA_BUCKET_NAME):
    em = EmmaaModel.load_from_s3(model_name, bucket=bucket)
    em.run_assembly()
    tests = [StatementCheckingTest(stmt) for stmt in em.assembled_stmts if
             all(stmt.agent_list())]
    if upload:
        date_str = make_date_str()
        client = get_s3_client(unsigned=False)
        client.put_object(Body=pickle.dumps(tests), Bucket=bucket,
                          Key=f'tests/{model_name}_tests_{date_str}.pkl')
    return tests


def run_model_tests_from_s3(model_name, test_corpus='large_corpus_tests',
                            upload_results=True, bucket=EMMAA_BUCKET_NAME):
    """Run a given set of tests on a given model, both loaded from S3.

    After loading both the model and the set of tests, model/test overlap
    is determined using a ScopeTestConnector and tests are run.


    Parameters
    ----------
    model_name : str
        Name of EmmaaModel to load from S3.
    test_corpus : str
        Name of the file containing tests on S3.
    upload_results : Optional[bool]
        Whether to upload test results to S3 in JSON format. Can be set
        to False when running tests. Default: True

    Returns
    -------
    emmaa.model_tests.ModelManager
        Instance of ModelManager containing the model data, list of applied
        tests and the test results.
    """
    mm = load_model_manager_from_s3(model_name=model_name, bucket=bucket)
    tests, _ = load_tests_from_s3(test_corpus, bucket=bucket)
    tm = TestManager([mm], tests)
    tm.make_tests(ScopeTestConnector())
    tm.run_tests()
    # Optionally upload test results to S3
    if upload_results:
        mm.upload_results(test_corpus, bucket=bucket)
    return mm
