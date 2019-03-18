import json
import logging
import jsonpickle
import datetime
from collections import defaultdict
from emmaa.util import (find_latest_s3_file, find_second_latest_s3_file,
                        find_latest_s3_files, find_number_of_files_on_s3,
                        make_date_str, get_s3_client)
from indra.statements.statements import Statement
from indra.assemblers.english.assembler import EnglishAssembler


logger = logging.getLogger(__name__)


CONTENT_TYPE_FUNCTION_MAPPING = {
    'statements': ('get_stmt_hashes', 'get_english_statement_by_hash'),
    'applied_tests': ('get_applied_test_hashes', 'get_english_test_by_hash'),
    'passed_tests': ('get_passed_test_hashes', 'get_english_test_by_hash'),
    'paths': ('get_passed_test_hashes', 'get_path_by_hash')
}


class TestRound(object):
    """Analyzes the results of one test round.

    Parameters
    ----------
    json_results : list[dict]
        A list of JSON formatted dictionaries to store information about the
        test results. The first dictionary contains information about the model.
        Each consecutive dictionary contains information about a single test
        applied to the model and test results.

    Attributes
    ----------
    statements : list[indra.statements.Statement]
        A list of INDRA Statements used to assemble a model.
    test_results : list[indra.explanation.model_checker.PathResult]
        A list of EMMAA test results.
    tests : list[indra.statements.Statement]
        A list of INDRA Statements used to make EMMAA tests.
    function_mapping : dict
        A dictionary of strings mapping a type of content to a tuple of
        functions necessary to find delta for this type of content. First
        function in a tuple gets a list of all hashes for a given content type,
        while the second returns an English description of a given content type
        for a single hash.
    """
    def __init__(self, json_results):
        self.json_results = json_results
        self.statements = self._get_statements()
        self.test_results = self._get_results()
        self.tests = self._get_tests()
        self.function_mapping = CONTENT_TYPE_FUNCTION_MAPPING

    @classmethod
    def load_from_s3_key(cls, key):
        client = get_s3_client()
        logger.info(f'Loading test results from {key}')
        obj = client.get_object(Bucket='emmaa', Key=key)
        json_results = json.loads(obj['Body'].read().decode('utf8'))
        test_round = TestRound(json_results)
        return test_round

    # Model Summary Methods
    def get_total_statements(self):
        """Return a total number of statements in a model."""
        return len(self.statements)

    def get_stmt_hashes(self):
        """Return a list of hashes for all statements in a model."""
        return [str(stmt.get_hash()) for stmt in self.statements]

    def get_statement_types(self):
        """Return a sorted list of tuples containing a statement type and a 
        number of times a statement of this type occured in a model.
        """
        statement_types = defaultdict(int)
        for stmt in self.statements:
            statement_types[type(stmt).__name__] += 1
        return sorted(statement_types.items(), key=lambda x: x[1], reverse=True)

    def get_agent_distribution(self):
        """Return a sorted list of tuples containing an agent name and a number
        of times this agent occured in statements of a model."""
        agent_count = defaultdict(int)
        for stmt in self.statements:
            for agent in stmt.agent_list():
                if agent is not None:
                    agent_count[agent.name] += 1
        return sorted(agent_count.items(), key=lambda x: x[1], reverse=True)

    def get_statements_by_evidence(self):
        """Return a sorted list of tuples containing a statement hash and a
        number of times this statement occured in a model."""
        stmts_evidence = {}
        for stmt in self.statements:
            stmts_evidence[str(stmt.get_hash())] = len(stmt.evidence)
        return sorted(stmts_evidence.items(), key=lambda x: x[1], reverse=True)

    def get_english_statements_by_hash(self):
        """Return a dictionary mapping a statement and its English description."""
        stmts_by_hash = {}
        for stmt in self.statements:
            stmts_by_hash[str(stmt.get_hash())] = self.get_english_statement(stmt)
        return stmts_by_hash

    def get_english_statement(self, stmt):
        ea = EnglishAssembler([stmt])
        return ea.make_model()

    def get_english_statement_by_hash(self, stmt_hash):
        return self.get_english_statements_by_hash()[stmt_hash]

    # Test Summary Methods
    def get_applied_test_hashes(self):
        """Return a list of hashes for all applied tests."""
        return [str(test.get_hash()) for test in self.tests]

    def get_passed_test_hashes(self):
        """Return a list of hashes for passed tests."""
        passed_tests = []
        for ix, result in enumerate(self.test_results):
            if result.path_found:
                passed_tests.append(str(self.tests[ix].get_hash()))
        return passed_tests

    def get_total_applied_tests(self):
        """Return a number of all applied tests."""
        return len(self.tests)

    def get_number_passed_tests(self):
        """Return a number of all passed tests."""
        return len(self.get_passed_test_hashes())

    def passed_over_total(self):
        """Return a ratio of passed over total tests."""
        return self.get_number_passed_tests()/self.get_total_applied_tests()

    def get_english_tests(self):
        """Return a dictionary mapping a test hash and its English description."""
        tests_by_hash = {}
        for test in self.tests:
            tests_by_hash[str(test.get_hash())] = self.get_english_statement(test)
        return tests_by_hash

    def get_path_descriptions(self):
        """Return a dictionary mapping a test hash and an English desciption of
        a path found."""
        paths = {}
        for ix, result in enumerate(self.test_results):
            if result.path_found:
                paths[str(self.tests[ix].get_hash())] = (
                    self.json_results[ix+1]['english_result'])
        return paths

    def get_english_test_by_hash(self, test_hash):
        return self.get_english_tests()[test_hash]

    def get_path_by_hash(self, test_hash):
        return self.get_path_descriptions()[test_hash]

    # Methods to find delta
    def find_numeric_delta(self, other_round, one_round_numeric_func):
        """Find a numeric delta between two rounds using a passed function.

        Parameters
        ----------
        other_round : emmaa.analyze_tests_results.TestRound
            A different instance of a TestRound
        one_round_numeric_function : str
            A name of a method to calculate delta. Accepted values:
            - get_total_statements
            - get_total_applied_tests
            - get_number_passed_tests
            - passed_over_total

        Returns
        -------
        delta : int or float
            Difference between return values of one_round_numeric_function
            of two given test rounds.
        """
        delta = (getattr(self, one_round_numeric_func)()
                 - getattr(other_round, one_round_numeric_func)())
        return delta

    def find_content_delta(self, other_round, content_type):
        """Return a dictionary of changed items of a given content type. This
        method makes use of self.function_mapping dictionary.

        Parameters
        ----------
        other_round : emmaa.analyze_tests_results.TestRound
            A different instance of a TestRound
        content_type : str
            A type of the content to find delta. Accepted values:
            - statements
            - appied_tests
            - passed_tests
            - paths

        Returns
        -------
            A dictionary containing lists of added and removed items of a given
            content type between two test rounds.
        """
        latest_hashes = getattr(self, self.function_mapping[content_type][0])()
        previous_hashes = getattr(
            other_round, other_round.function_mapping[content_type][0])()
        added_hashes = list(set(latest_hashes) - set(previous_hashes))
        removed_hashes = list(set(previous_hashes) - set(latest_hashes))
        added_items = [getattr(
            self, self.function_mapping[content_type][1])(item_hash)
            for item_hash in added_hashes]
        removed_items = [getattr(
            other_round,
            other_round.function_mapping[content_type][1])(item_hash)
            for item_hash in removed_hashes]
        return {'added': added_items, 'removed': removed_items}

    # Helping methods
    def _get_statements(self):
        serialized_stmts = self.json_results[0]['statements']
        return [Statement._from_json(stmt) for stmt in serialized_stmts]

    def _get_results(self):
        unpickler = jsonpickle.unpickler.Unpickler()
        test_results = [unpickler.restore(result['result_json'])
                        for result in self.json_results[1:]]
        return test_results

    def _get_tests(self):
        tests = [Statement._from_json(res['test_json'])
                 for res in self.json_results[1:]]
        return tests


class StatsGenerator(object):
    """Generates statistic for a given test round.
    Parameters
    ----------
    model_name : str
        A name of a model the tests were run against.
    latest_round : emmaa.analyze_tests_results.TestRound
        An instance of a TestRound to generate statistics for. If not given,
        will be generated by loading test results from s3.
    previous_round : emmaa.analyze_tests_results.TestRound
        A different instance of a TestRound to find delta between two rounds.
        If not given, will be generated by loading test results from s3.

    Attributes
    ----------
    json_stats : dict
        A JSON-formatted dictionary containing test model and test statistics.
    earlier_json_stats : list[dict]
        A list of JSON-formatted dictionaries containing test model and test
        statistics for earlier test rounds.
    """

    def __init__(self, model_name, latest_round=None, previous_round=None):
        self.model_name = model_name
        if not latest_round:
            self.latest_round = self._get_latest_round()
        else:
            self.latest_round = latest_round
        if not previous_round:
            self.previous_round = self._get_previous_round()
        else:
            self.previous_round = previous_round
        self.json_stats = {}
        self.previous_json_stats = self._get_previous_json_stats()

    def make_stats(self):
        """Check if two latest test rounds were found and add statistics to
        json_stats dictionary. If both latest round and previous round
        were passed or found on s3, a dictionary will have four key-value
        pairs: model_summary, test_round_summary, model_delta, and tests_delta.
        """
        if not self.latest_round:
            logger.info(f'Latest round for {self.model_name} is not found.')
            return
        self.make_model_summary()
        self.make_test_summary()
        if not self.previous_round:
            logger.info(f'Previous round for {self.model_name} is not found.')
            return
        self.make_model_delta()
        self.make_tests_delta()
        self.make_changes_over_time()

    def make_model_summary(self):
        """Add latest model state summary to json_stats."""
        self.json_stats['model_summary'] = {
            'model_name': self.model_name,
            'number_of_statements': self.latest_round.get_total_statements(),
            'stmts_type_distr': self.latest_round.get_statement_types(),
            'agent_distr': self.latest_round.get_agent_distribution(),
            'stmts_by_evidence': self.latest_round.get_statements_by_evidence(),
            'english_stmts': self.latest_round.get_english_statements_by_hash()
        }

    def make_test_summary(self):
        """Add latest test round summary to json_stats."""
        self.json_stats['test_round_summary'] = {
            'number_applied_tests': self.latest_round.get_total_applied_tests(),
            'number_passed_tests': self.latest_round.get_number_passed_tests(),
            'passed_ratio': self.latest_round.passed_over_total(),
            'tests_by_hash': self.latest_round.get_english_tests(),
            'passed_tests': self.latest_round.get_passed_test_hashes(),
            'paths': self.latest_round.get_path_descriptions()
        }

    def make_model_delta(self):
        """Add model delta between two latest model states to json_stats."""
        self.json_stats['model_delta'] = {
            'number_of_statements_delta': self.latest_round.find_numeric_delta(
                self.previous_round, 'get_total_statements'),
            'statements_delta': self.latest_round.find_content_delta(
                self.previous_round, 'statements')
        }

    def make_tests_delta(self):
        """Add tests delta between two latest test rounds to json_stats."""
        self.json_stats['tests_delta'] = {
            'number_applied_tests_delta': self.latest_round.find_numeric_delta(
                self.previous_round, 'get_total_applied_tests'),
            'number_passed_tests_delta': self.latest_round.find_numeric_delta(
                self.previous_round, 'get_number_passed_tests'),
            'passed_ratio_delta': self.latest_round.find_numeric_delta(
                self.previous_round, 'passed_over_total'),
            'applied_tests_delta': self.latest_round.find_content_delta(
                self.previous_round, 'applied_tests'),
            'pass_fail_delta': self.latest_round.find_content_delta(
                self.previous_round, 'passed_tests'),
            'new_paths': self.latest_round.find_content_delta(
                self.previous_round, 'paths')
        }

    def make_changes_over_time(self):
        """Add changes to model and tests over time to json_stats."""
        self.json_stats['changes_over_time'] = {
            'number_of_statements': self.get_over_time(
                'model_summary', 'number_of_statements'),
            'number_applied_tests': self.get_over_time(
                'test_round_summary', 'number_applied_tests'),
            'number_passed_tests': self.get_over_time(
                'test_round_summary', 'number_passed_tests'),
            'passed_ratio': self.get_over_time(
                'test_round_summary', 'passed_ratio')
        }

    def get_over_time(self, section, metrics):
        if not self.previous_json_stats:
            previous_data = []
        else:
            previous_data = (
                self.previous_json_stats['changes_over_time'][metrics])
        previous_data.append(self.json_stats[section][metrics])
        return previous_data

    def save_to_s3(self):
        json_stats_str = json.dumps(self.json_stats, indent=1)
        client = get_s3_client()
        date_str = make_date_str(datetime.datetime.now())
        stats_key = f'stats/{self.model_name}/stats_{date_str}.json'
        logger.info(f'Uploading test round statistics to {stats_key}')
        client.put_object(Bucket='emmaa', Key=stats_key,
                          Body=json_stats_str.encode('utf8'))

    def _get_latest_round(self):
        latest_key = find_latest_s3_file(
            'emmaa', f'results/{self.model_name}/results_', extension='.json')
        if latest_key is None:
            logger.info(f'Could not find a key to the latest test results '
                        f'for {self.model_name} model.')
            return
        tr = TestRound.load_from_s3_key(latest_key)
        return tr

    def _get_previous_round(self):
        previous_key = find_second_latest_s3_file(
            'emmaa', f'results/{self.model_name}/results_', extension='.json')
        if previous_key is None:
            logger.info(f'Could not find a key to the previous test results '
                        f'for {self.model_name} model.')
            return
        tr = TestRound.load_from_s3_key(previous_key)
        return tr

    def _get_previous_json_stats(self):
        key = find_latest_s3_file(
            'emmaa', f'stats/{self.model_name}/stats_', extension='.json')
        if key is None:
            logger.info(f'Could not find a key to the previous statistics '
                        f'for {self.model_name} model.')
            return
        client = get_s3_client()
        logger.info(f'Loading earlier statistics from {key}')
        obj = client.get_object(Bucket='emmaa', Key=key)
        previous_json_stats = json.loads(obj['Body'].read().decode('utf8'))
        return previous_json_stats
