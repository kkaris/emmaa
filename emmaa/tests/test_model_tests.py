from nose.plugins.attrib import attr

from indra.statements import *
from indra.explanation.model_checker import PathResult, PysbModelChecker, \
    PybelModelChecker, SignedGraphModelChecker, UnsignedGraphModelChecker
from emmaa.model import EmmaaModel
from emmaa.model_tests import StatementCheckingTest, ModelManager, \
    ScopeTestConnector, TestManager, RefinementTestConnector
from emmaa.analyze_tests_results import TestRound, StatsGenerator
from emmaa.tests.test_model import create_model


# Tell nose to not run tests in the imported modules
StatementCheckingTest.__test__ = False
TestRound.__test__ = False
ScopeTestConnector.__test__ = False
TestManager.__test__ = False


def test_model_manager_structure():
    model = create_model()
    mm = ModelManager(model)
    assert isinstance(mm, ModelManager)
    assert isinstance(mm.model, EmmaaModel)
    assert mm.model.name == 'test'
    assert len(mm.mc_types) == 4, len(mm.mc_types)
    assert len(mm.mc_types['pysb']) == 3
    assert isinstance(mm.mc_types['pysb']['model_checker'], PysbModelChecker)
    assert isinstance(mm.mc_types['pybel']['model_checker'], PybelModelChecker)
    assert isinstance(
        mm.mc_types['signed_graph']['model_checker'], SignedGraphModelChecker)
    assert isinstance(
        mm.mc_types['unsigned_graph']['model_checker'],
        UnsignedGraphModelChecker)
    assert isinstance(mm.entities[0], Agent)
    assert isinstance(mm.date_str, str)


def test_run_tests():
    model = create_model()
    tests = [StatementCheckingTest(
             Activation(Agent('BRAF', db_refs={'HGNC': '1097'}),
                        Agent('MAPK1', db_refs={'UP': 'P28482'})))]
    mm = ModelManager(model)
    tm = TestManager([mm], tests)
    tm.make_tests(ScopeTestConnector())
    tm.run_tests()
    assert len(mm.applicable_tests) == 1
    assert isinstance(mm.applicable_tests[0], StatementCheckingTest)
    assert len(mm.mc_types['pysb']['test_results']) == 1
    assert len(mm.mc_types['pybel']['test_results']) == 1
    assert len(mm.mc_types['signed_graph']['test_results']) == 1
    assert len(mm.mc_types['unsigned_graph']['test_results']) == 1
    assert isinstance(mm.mc_types['pysb']['test_results'][0], PathResult)


def test_applicability():
    model = create_model()
    tests = [StatementCheckingTest(
                Activation(Agent('BRAF', db_refs={'HGNC': '1097'}),
                           Agent('MAPK1', db_refs={'UP': 'P28482'}))),
             StatementCheckingTest(
                Activation(Agent('BRAF', db_refs={'HGNC': '1097'}),
                           Agent('ERK', db_refs={'FPLX': 'ERK'})))]
    mm = ModelManager(model)
    tm = TestManager([mm], tests)
    # Only first test is applicable with ScopeTestConnector
    tm.make_tests(ScopeTestConnector())
    assert len(mm.applicable_tests) == 1
    assert mm.applicable_tests[0] == tests[0]
    # Both tests are applicable with RefinementTestConnector
    mm.applicable_tests = []
    tm.make_tests(RefinementTestConnector())
    assert len(mm.applicable_tests) == 2


def test_paths_content():
    model = create_model()
    model.run_assembly()
    # Add statements with similar subject and object to test grouping
    map2k1 = model.assembled_stmts[1].subj
    mapk1 = model.assembled_stmts[1].obj
    phos = Phosphorylation(map2k1, mapk1)
    phos_t185 = Phosphorylation(map2k1, mapk1, 'T', '185')
    phos_y187 = Phosphorylation(map2k1, mapk1, 'Y', '187')
    inc = IncreaseAmount(map2k1, mapk1)
    inh = Inhibition(map2k1, mapk1)
    model.assembled_stmts += [phos, phos_t185, phos_y187, inc, inh]
    mm = ModelManager(model)
    tests = [StatementCheckingTest(
             Activation(Agent('BRAF', db_refs={'HGNC': '1097'}),
                        Agent('MAPK1', db_refs={'UP': 'P28482'})))]
    tm = TestManager([mm], tests)
    tm.make_tests(ScopeTestConnector())
    tm.run_tests()
    result_json, json_lines = mm.results_to_json()
    assert len(result_json) == 2
    assert len(result_json[1]) == 6, len(result_json[1])
    # The second edge will be supported differently in different model types
    assert result_json[1]['pysb']['path_json'][0]['path'] == \
        'BRAF → MAP2K1 → MAPK1'
    second_edge = result_json[1]['pysb']['path_json'][0]['edge_list'][1]
    # Only Activation statement will be in the edge in PySB
    assert len(second_edge['stmts']) == 1
    assert second_edge['stmts'][0][1] == 'Active MAP2K1 activates MAPK1.'
    assert second_edge['stmts'][0][0].count('stmt_hash') == 1
    # Positive (Activation and IncreaseAmount) in SignedGraph edge
    assert result_json[1]['signed_graph']['path_json'][0]['path'] == \
        'BRAF → MAP2K1 → MAPK1'
    second_edge = result_json[1]['signed_graph']['path_json'][0][
        'edge_list'][1]
    assert len(second_edge['stmts']) == 2
    assert second_edge['stmts'][0][1] == 'MAP2K1 activates MAPK1.'
    assert second_edge['stmts'][0][0].count('stmt_hash') == 1
    assert second_edge['stmts'][1][1] == ('MAP2K1 increases the amount of '
                                          'MAPK1.')
    assert second_edge['stmts'][1][0].count('stmt_hash') == 1
    # All statement types support unsigned graph edge, but different statements
    # of the same type are grouped together
    assert result_json[1]['unsigned_graph']['path_json'][0]['path'] == \
        'BRAF → MAP2K1 → MAPK1'
    second_edge = result_json[1]['unsigned_graph']['path_json'][0][
        'edge_list'][1]
    assert len(second_edge['stmts']) == 4
    assert second_edge['stmts'][0][1] == 'MAP2K1 activates MAPK1.'
    assert second_edge['stmts'][0][0].count('stmt_hash') == 1
    assert second_edge['stmts'][1][1] == 'MAP2K1 phosphorylates MAPK1.'
    assert second_edge['stmts'][1][0].count('stmt_hash') == 3
    assert second_edge['stmts'][2][1] == 'MAP2K1 inhibits MAPK1.'
    assert second_edge['stmts'][2][0].count('stmt_hash') == 1
    assert second_edge['stmts'][3][1] == ('MAP2K1 increases the amount of '
                                          'MAPK1.')
    assert second_edge['stmts'][3][0].count('stmt_hash') == 1
    # Test JSONL representation
    assert len(json_lines) == 3
    for path_dict in json_lines:
        assert len(path_dict['edges']) == 2
        assert len(path_dict['edges'][0]) == 1
        if path_dict['graph_type'] == 'pysb':
            assert len(path_dict['edges'][1]) == 1
        elif path_dict['graph_type'] == 'signed_graph':
            assert len(path_dict['edges'][1]) == 2
        elif path_dict['graph_type'] == 'unsigned_graph':
            assert len(path_dict['edges'][1]) == 6
