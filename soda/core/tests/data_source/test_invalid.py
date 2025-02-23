from tests.helpers.common_test_tables import customers_test_table
from tests.helpers.scanner import Scanner


def test_default_invalid(scanner: Scanner):
    table_name = scanner.ensure_test_table(customers_test_table)

    scan = scanner.create_test_scan()
    scan.add_sodacl_yaml_str(
        f"""
      checks for {table_name}:
        - invalid_count(id) = 0
        - valid_count(id) = 9
    """
    )
    scan.execute_unchecked()

    scan.assert_log_warning("Counting invalid without valid specification does not make sense")
    scan.assert_all_checks_pass()


def test_column_configured_invalid_values(scanner: Scanner):
    table_name = scanner.ensure_test_table(customers_test_table)

    scan = scanner.create_test_scan()
    scan.add_sodacl_yaml_str(
        f"""
      checks for {table_name}:
        - invalid_count(id) = 6
        - valid_count(id) = 3
      configurations for {table_name}:
        valid values for id:
         - ID1
         - ID2
         - ID3
    """
    )
    scan.execute()

    scan.assert_all_checks_pass()


def test_valid_min_max(scanner: Scanner):
    table_name = scanner.ensure_test_table(customers_test_table)

    scan = scanner.create_test_scan()
    scan.add_sodacl_yaml_str(
        f"""
      checks for {table_name}:
        - invalid_count(size) = 3:
            valid min: 0
        - invalid_count(size) = 4:
            valid max: 0
    """
    )
    scan.execute()
    scan.assert_all_checks_pass()


def test_valid_format_email(scanner: Scanner):
    table_name = scanner.ensure_test_table(customers_test_table)

    scan = scanner.create_test_scan()
    scan.add_sodacl_yaml_str(
        f"""
          checks for {table_name}:
            - invalid_count(email) = 1:
                valid format: email
            - missing_count(email) = 5
        """
    )
    scan.execute()

    scan.assert_all_checks_pass()


def test_column_configured_invalid_and_missing_values(scanner: Scanner):
    table_name = scanner.ensure_test_table(customers_test_table)

    scan = scanner.create_test_scan()
    scan.add_sodacl_yaml_str(
        f"""
          checks for {table_name}:
            - missing_count(pct) = 3
            - invalid_count(pct) = 1
            - valid_count(pct) = 6
          configurations for {table_name}:
            missing values for pct: ['N/A', 'No value']
            valid format for pct: percentage
        """
    )
    scan.execute()

    scan.assert_all_checks_pass()


def test_valid_length(scanner: Scanner):
    table_name = scanner.ensure_test_table(customers_test_table)

    scan = scanner.create_test_scan()
    scan.add_sodacl_yaml_str(
        f"""
          checks for {table_name}:
            - invalid_count(cat) = 2
            - valid_count(cat) = 3
          configurations for {table_name}:
            valid min length for cat: 4
            valid max length for cat: 4
        """
    )
    scan.execute()

    scan.assert_all_checks_pass()

    scan = scanner.create_test_scan()
    scan.add_sodacl_yaml_str(
        f"""
          checks for {table_name}:
            - invalid_count(cat) = 2
            - valid_count(cat) = 3
          configurations for {table_name}:
            valid length for cat: 4
        """
    )
    scan.execute()

    scan.assert_all_checks_pass()


def test_check_and_column_configured_invalid_values(scanner: Scanner):
    """
    In case both column *and* check configurations are specified, they both are applied.
    """
    table_name = scanner.ensure_test_table(customers_test_table)

    digit_regex = scanner.data_source.escape_regex(r"ID\d")

    scan = scanner.create_test_scan()
    scan.add_sodacl_yaml_str(
        f"""
          checks for {table_name}:
            - valid_count(id) = 9
            - valid_count(id) = 2:
                valid values:
                 - ID1
                 - ID2
            - invalid_count(id) = 0
            - invalid_count(id) = 7:
                valid values:
                 - ID1
                 - ID2
          configurations for {table_name}:
            valid regex for id: {digit_regex}
        """
    )
    scan.execute()

    scan.assert_all_checks_pass()
