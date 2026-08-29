from tools import check_documentation_contracts


def test_normative_documentation_matches_executable_contracts():
    assert check_documentation_contracts.find_documentation_contract_issues() == ()
