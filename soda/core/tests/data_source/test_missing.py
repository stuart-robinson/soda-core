from tests.helpers.common_test_tables import customers_test_table
from tests.helpers.scanner import Scanner


def test_default_missing(scanner: Scanner):
    table_name = scanner.ensure_test_table(customers_test_table)

    scan = scanner.create_test_scan()
    scan.add_sodacl_yaml_str(
        f"""
          checks for {table_name}:
            - missing_count(id) = 1
        """
    )
    scan.execute()

    scan.assert_all_checks_pass()


def test_column_configured_missing_values(scanner: Scanner):
    table_name = scanner.ensure_test_table(customers_test_table)

    scan = scanner.create_test_scan()
    scan.add_sodacl_yaml_str(
        f"""
      checks for {table_name}:
        - missing_count(id) = 2
      configurations for {table_name}:
        missing values for id: [N/A, ID5]
    """
    )
    scan.execute()

    scan.assert_all_checks_pass()


def test_check_configured_missing_values(scanner: Scanner):
    table_name = scanner.ensure_test_table(customers_test_table)

    scan = scanner.create_test_scan()
    scan.add_sodacl_yaml_str(
        f"""
      checks for {table_name}:
        - missing_count(id) = 2:
            missing values:
             - ID5
    """
    )
    scan.execute()

    scan.assert_all_checks_pass()


def test_check_and_column_configured_missing_values(scanner: Scanner):
    """
    In case both column *and* check configurations are specified, they both are applied.

    So in the case below, the total list of missing values applied in the check on column id is
       NULL, 'ID1', 'ID2', 'ID5', 'N/A'

    SQL for this check metric:
      SELECT
        COUNT(CASE WHEN "id" IS NULL OR "id" IN ('ID1','ID2') OR "id" IN ('N/A','ID5') THEN 1 END)
      FROM "sodasql"."public"."SODATEST_CUSTOMERS_4ec6d529"
    """
    table_name = scanner.ensure_test_table(customers_test_table)

    scan = scanner.create_test_scan()
    scan.add_sodacl_yaml_str(
        f"""
      checks for {table_name}:
        - missing_count(id) = 4:
            missing values:
              - ID1
              - ID2
      configurations for {table_name}:
        missing values for id: [N/A, ID5]
    """
    )
    scan.execute()

    scan.assert_all_checks_pass()
