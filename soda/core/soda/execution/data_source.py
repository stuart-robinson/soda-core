from __future__ import annotations

import datetime
import hashlib
import importlib
import json
import re
from datetime import date, datetime
from math import ceil, floor
from numbers import Number
from textwrap import dedent

from soda.common.exceptions import DataSourceError
from soda.common.logs import Logs
from soda.execution.data_type import DataType
from soda.execution.partition_queries import PartitionQueries
from soda.execution.query import Query
from soda.execution.schema_query import TableColumnsQuery
from soda.sampler.sample_ref import SampleRef
from soda.telemetry.soda_telemetry import SodaTelemetry

soda_telemetry = SodaTelemetry.get_instance()


class DataSource:

    """
    Implementing a DataSource:
    @m1n0, can you add a checklist here of places where DataSource implementors need to make updates to add
    a new DataSource?

    Validation of the connection configuration properties:
    The DataSource impl is only responsible to raise an exception with an appropriate message in te #connect()
    See that abstract method below for more details.
    """

    # Maps synonym types for the convenience of use in checks.
    # Keys represent the data_source type, values are lists of "aliases" that can be used in SodaCL as synonyms.
    SCHEMA_CHECK_TYPES_MAPPING: dict = {
        "character varying": ["varchar"],
    }
    SQL_TYPE_FOR_CREATE_TABLE_MAP: dict = {
        DataType.TEXT: "VARCHAR(255)",
        DataType.INTEGER: "INT",
        DataType.DECIMAL: "FLOAT",
        DataType.DATE: "DATE",
        DataType.TIME: "TIME",
        DataType.TIMESTAMP: "TIMESTAMP",
        DataType.TIMESTAMP_TZ: "TIMESTAMPTZ",
        DataType.BOOLEAN: "BOOLEAN",
    }

    SQL_TYPE_FOR_SCHEMA_CHECK_MAP = {
        DataType.TEXT: "character varying",
        DataType.INTEGER: "integer",
        DataType.DECIMAL: "double precision",
        DataType.DATE: "date",
        DataType.TIME: "time",
        DataType.TIMESTAMP: "timestamp without time zone",
        DataType.TIMESTAMP_TZ: "timestamp with time zone",
        DataType.BOOLEAN: "boolean",
    }

    NUMERIC_TYPES_FOR_PROFILING = ["integer", "double precision"]
    TEXT_TYPES_FOR_PROFILING = ["character varying"]

    @staticmethod
    def create(
        logs: Logs,
        data_source_name: str,
        connection_type: str,
        data_source_properties: dict,
        connection_properties: dict,
    ) -> DataSource:
        """
        The returned data_source does not have a connection.  It is the responsibility of
        the caller to initialize data_source.connection.  To create a new connection,
        use data_source.connect(...)
        """
        module_name = f"soda.data_sources.{connection_type}_data_source"
        data_source_properties["connection_type"] = connection_type
        try:
            module = importlib.import_module(module_name)
            return module.DataSourceImpl(logs, data_source_name, data_source_properties, connection_properties)
        except ModuleNotFoundError as e:
            if connection_type == "postgresql":
                logs.error(f'Data source type "{connection_type}" not found. Did you mean postgres?')
            else:
                raise DataSourceError(
                    f'Data source type "{connection_type}" not found. Did you spell {connection_type} correctly? Did you install module soda-core-{connection_type}?'
                )
            return None

    def __init__(
        self,
        logs: Logs,
        data_source_name: str,
        data_source_properties: dict,
        connection_properties: dict,
    ):
        self.logs = logs
        self.data_source_name = data_source_name
        self.data_source_properties: dict = data_source_properties
        self.connection_properties = connection_properties
        # Pep 249 compliant connection object (aka DBAPI)
        # https://www.python.org/dev/peps/pep-0249/#connection-objects
        # @see self.connect() for initialization
        self.type = self.data_source_properties.get("connection_type")
        self.connection = None
        self.database: str = data_source_properties.get("database")
        self.schema: str | None = data_source_properties.get("schema")
        self.table_prefix = data_source_properties.get("table_prefix")
        # self.data_source_scan is initialized in create_data_source_scan(...) below
        self.data_source_scan: DataSourceScan | None = None

    def create_data_source_scan(self, scan: Scan, data_source_scan_cfg: DataSourceScanCfg):
        from soda.execution.data_source_scan import DataSourceScan

        data_source_scan = DataSourceScan(scan, data_source_scan_cfg, self)
        self.data_source_scan = data_source_scan

        return self.data_source_scan

    def validate_configuration(self, connection_properties: dict, logs: Logs) -> None:
        """
        validates connection_properties and self.data_source_properties
        """
        raise NotImplementedError(f"TODO: Implement {type(self)}.validate_configuration(...)")

    def get_type_name(self, type_code):
        return str(type_code)

    def create_partition_queries(self, partition):
        return PartitionQueries(partition)

    def is_supported_metric_name(self, metric_name: str) -> bool:
        return (
            metric_name in ["row_count", "missing_count", "invalid_count", "valid_count", "duplicate_count"]
            or self.get_metric_sql_aggregation_expression(metric_name, None, None) is not None
        )

    def get_metric_sql_aggregation_expression(self, metric_name: str, metric_args: list[object] | None, expr: str):
        if "min" == metric_name:
            return self.expr_min(expr)
        if "max" == metric_name:
            return self.expr_max(expr)
        if "avg" == metric_name:
            return self.expr_avg(expr)
        if "sum" == metric_name:
            return self.expr_sum(expr)
        if "min_length" == metric_name:
            return self.expr_min(self.expr_length(expr))
        if "max_length" == metric_name:
            return self.expr_max(self.expr_length(expr))
        if "avg_length" == metric_name:
            return self.expr_avg(self.expr_length(expr))
        return None

    def is_same_type_in_schema_check(self, expected_type: str, actual_type: str):
        expected_type = expected_type.strip().lower()

        if (
            actual_type in self.SCHEMA_CHECK_TYPES_MAPPING
            and expected_type in self.SCHEMA_CHECK_TYPES_MAPPING[actual_type]
        ):
            return True

        return expected_type == actual_type

    def qualify_table_name(self, table_name: str) -> str:
        return table_name

    @staticmethod
    def column_metadata_columns() -> list:
        return ["column_name", "data_type", "is_nullable"]

    @staticmethod
    def column_metadata_catalog_column() -> str:
        return "table_catalog"

    ######################
    # Store Table Sample
    ######################

    def store_table_sample(self, table_name: str, limit: int | None = None) -> SampleRef:
        sql = self.sql_select_all(table_name=table_name, limit=limit)
        query = Query(
            data_source_scan=self.data_source_scan,
            unqualified_query_name=f"store-sample-for-{table_name}",
            sql=sql,
            sample_name="table_sample",
        )
        query.store()
        return query.sample_ref

    def sql_select_all(self, table_name: str, limit: int | None = None) -> str:
        quoted_table_name = self.quote_table(table_name)
        limit_sql = ""
        if limit is not None:
            limit_sql = f" \n LIMIT {limit}"
        sql = f"SELECT * FROM {quoted_table_name}{limit_sql}"
        return sql

    ############################################
    # For a table, get the columns metadata
    ############################################

    def get_table_columns(
        self,
        table_name: str,
        query_name: str,
        included_columns: list[str] | None = None,
        excluded_columns: list[str] | None = None,
    ) -> dict[str, str] | None:
        """
        :return: A dict mapping column names to data source data types.  Like eg
        {"id": "varchar", "size": "int8", ...}
        """
        query = Query(
            data_source_scan=self.data_source_scan,
            unqualified_query_name=query_name,
            sql=self.sql_get_table_columns(
                table_name, included_columns=included_columns, excluded_columns=excluded_columns
            ),
        )
        query.execute()
        if len(query.rows) > 0:
            return {row[0]: row[1] for row in query.rows}
        return None

    def create_table_columns_query(self, partition: Partition, schema_metric: SchemaMetric) -> TableColumnsQuery:
        return TableColumnsQuery(partition, schema_metric)

    def sql_get_table_columns(
        self, table_name: str, included_columns: list[str] | None = None, excluded_columns: list[str] | None = None
    ) -> str:
        # build optional filter clauses
        if self.database:
            database_filter = f" \n  AND lower({self.column_metadata_catalog_column()}) = '{self.database.lower()}'"
        else:
            database_filter = ""

        if self.schema:
            schema_filter = f" \n  AND lower(table_schema) = '{self.schema.lower()}'"
        else:
            schema_filter = ""

        if included_columns:
            included_columns_filter = ""
            for col in included_columns:
                included_columns_filter += f"\n AND lower(column_name) LIKE lower('{col}')"
        else:
            included_columns_filter = ""

        if excluded_columns:
            excluded_columns_filter = ""
            for col in excluded_columns:
                excluded_columns_filter += f"\n AND lower(column_name) NOT LIKE lower('{col}')"
        else:
            excluded_columns_filter = ""

        # compose query template
        sql = (
            f"SELECT {', '.join(self.column_metadata_columns())} \n"
            f"FROM information_schema.columns \n"
            f"WHERE lower(table_name) = '{table_name.lower()}'"
            f"{database_filter}"
            f"{schema_filter}"
            f"{included_columns_filter}"
            f"{excluded_columns_filter}"
            "\nORDER BY ORDINAL_POSITION"
        )
        return sql

    ############################################
    # Get table names with count in one go
    ############################################

    def sql_get_table_names_with_count(
        self, include_tables: list[str] | None = None, exclude_tables: list[str] | None = None
    ) -> str:
        table_filter_expression = self.sql_table_include_exclude_filter(
            "relname", "schemaname", include_tables, exclude_tables
        )
        where_clause = f"\nWHERE {table_filter_expression} \n" if table_filter_expression else ""
        return f"SELECT relname, n_live_tup \n" f"FROM pg_stat_user_tables" f"{where_clause}"

    def sql_get_column(self, include_tables: list[str] | None = None, exclude_tables: list[str] | None = None) -> str:
        table_filter_expression = self.sql_table_include_exclude_filter(
            "table_name", "table_schema", include_tables, exclude_tables
        )
        where_clause = f"\nWHERE {table_filter_expression} \n" if table_filter_expression else ""
        return (
            f"SELECT table_name, column_name, data_type, is_nullable \n"
            f"FROM information_schema.columns"
            f"{where_clause}"
        )

    def sql_get_table_count(self, table_name: str) -> str:
        return f"SELECT {self.expr_count_all()} from {self.qualify_table_name(table_name)}"

    def sql_table_include_exclude_filter(
        self,
        table_column_name: str,
        schema_column_name: str | None = None,
        include_tables: list[str] = [],
        exclude_tables: list[str] = [],
    ) -> str | None:
        tablename_filter_clauses = []
        if include_tables:
            sql_include_clauses = " OR ".join(
                [f"lower({table_column_name}) like '{include_table.lower()}'" for include_table in include_tables]
            )
            tablename_filter_clauses.append(f"({sql_include_clauses})")

        if exclude_tables:
            tablename_filter_clauses.extend(
                [f"lower({table_column_name}) not like '{exclude_table.lower()}'" for exclude_table in exclude_tables]
            )

        if hasattr(self, "schema") and self.schema and schema_column_name:
            tablename_filter_clauses.append(f"lower({schema_column_name}) = '{self.schema.lower()}'")
        return "\n      AND ".join(tablename_filter_clauses) if tablename_filter_clauses else None

    def sql_find_table_names(
        self,
        filter: str | None = None,
        include_tables: list[str] = [],
        exclude_tables: list[str] = [],
        table_column_name: str = "table_name",
        schema_column_name: str = "table_schema",
    ) -> str:
        sql = f"SELECT table_name \n" f"FROM {self.sql_information_schema_identifier()}"
        where_clauses = []

        if filter:
            where_clauses.append(f"lower({table_column_name}) like '{filter.lower()}'")

        includes_excludes_filter = self.sql_table_include_exclude_filter(
            table_column_name, schema_column_name, include_tables, exclude_tables
        )
        if includes_excludes_filter:
            where_clauses.append(includes_excludes_filter)

        if where_clauses:
            where_clauses_sql = "\n  AND ".join(where_clauses)
            sql += f"\nWHERE {where_clauses_sql}"

        return sql

    def sql_information_schema_identifier(self) -> str:
        return "information_schema.tables"

    def sql_analyze_table(self, table: str) -> str | None:
        return None

    def cast_to_text(self, expr: str) -> str:
        return f"CAST({expr} AS VARCHAR)"

    def profiling_sql_values_frequencies_query(
        self,
        data_type_category: str,
        table_name: str,
        column_name: str,
        limit_mins_maxs: int,
        limit_frequent_values: int,
    ) -> str:
        cast_to_text = self.cast_to_text

        value_frequencies_cte = self.profiling_sql_value_frequencies_cte(table_name, column_name)

        union = self.sql_union()

        frequent_values_cte = f"""frequent_values AS (
                            SELECT {cast_to_text("'frequent_values'")} AS metric_, ROW_NUMBER() OVER(ORDER BY frequency_ DESC) AS index_, value_, frequency_
                            FROM value_frequencies
                            ORDER BY frequency_ desc
                            LIMIT {limit_frequent_values}
                        )"""

        if data_type_category == "text":
            return dedent(
                f"""
                    WITH
                        {value_frequencies_cte},
                        {frequent_values_cte}
                    SELECT *
                    FROM frequent_values
                    ORDER BY metric_ ASC, index_ ASC
                """
            )

        elif data_type_category == "numeric":

            mins_cte = f"""mins AS (
                            SELECT {cast_to_text("'mins'")} AS metric_, ROW_NUMBER() OVER(ORDER BY value_ ASC) AS index_, value_, frequency_
                            FROM value_frequencies
                            WHERE value_ IS NOT NULL
                            ORDER BY value_ ASC
                            LIMIT {limit_mins_maxs}
                        )"""

            maxs_cte = f"""maxs AS (
                            SELECT {cast_to_text("'maxs'")} AS metric_, ROW_NUMBER() OVER(ORDER BY value_ DESC) AS index_, value_, frequency_
                            FROM value_frequencies
                            WHERE value_ IS NOT NULL
                            ORDER BY value_ DESC
                            LIMIT {limit_mins_maxs}
                        )"""

            return dedent(
                f"""
                    WITH
                        {value_frequencies_cte},
                        {mins_cte},
                        {maxs_cte},
                        {frequent_values_cte},
                        result AS (
                            SELECT * FROM mins
                            {union}
                            SELECT * FROM maxs
                            {union}
                            SELECT * FROM frequent_values
                        )
                    SELECT *
                    FROM result
                    ORDER BY metric_ ASC, index_ ASC
                """
            )

        raise AssertionError("data_type_category must be either 'numeric' or 'text'")

    def sql_union(self):
        return "UNION"

    def profiling_sql_value_frequencies_cte(self, table_name: str, column_name: str) -> str:
        quoted_column_name = self.quote_column(column_name)
        quoted_table_name = self.quote_table(table_name)
        return f"""value_frequencies AS (
                            SELECT {quoted_column_name} AS value_, count(*) AS frequency_
                            FROM {quoted_table_name}
                            WHERE {quoted_column_name} IS NOT NULL
                            GROUP BY {quoted_column_name}
                        )"""

    def profiling_sql_aggregates_numeric(self, table_name: str, column_name: str) -> str:
        column_name = self.quote_column(column_name)
        table_name = self.quote_table(table_name)
        return dedent(
            f"""
            SELECT
                avg({column_name}) as average
                , sum({column_name}) as sum
                , variance({column_name}) as variance
                , stddev({column_name}) as standard_deviation
                , count(distinct({column_name})) as distinct_values
                , sum(case when {column_name} is null then 1 else 0 end) as missing_values
            FROM {table_name}
            """
        )

    def profiling_sql_aggregates_text(self, table_name: str, column_name: str) -> str:
        column_name = self.quote_column(column_name)
        table_name = self.quote_table(table_name)
        return dedent(
            f"""
            SELECT
                count(distinct({column_name})) as distinct_values
                , sum(case when {column_name} is null then 1 else 0 end) as missing_values
                , avg(length({column_name})) as avg_length
                , min(length({column_name})) as min_length
                , max(length({column_name})) as max_length
            FROM {table_name}
            """
        )

    def histogram_sql_and_boundaries(
        self, table_name: str, column_name: str, min: int | float, max: int | float
    ) -> tuple[str | None, list[int | float]]:
        # TODO: make configurable or derive dynamically based on data quantiles etc.
        number_of_bins: int = 20

        if not min < max:
            self.logs.warning(
                f"Min of {column_name} on table: {table_name} must be smaller than max value. Min is {min}, and max is {max}"
            )
            return None, []

        min_value = floor(min * 1000) / 1000
        max_value = ceil(max * 1000) / 1000
        bin_width = (max_value - min_value) / number_of_bins

        boundary_start = min_value
        bins_list = [min_value]
        for _ in range(0, number_of_bins):
            boundary_start += bin_width
            bins_list.append(round(boundary_start, 3))

        field_clauses = []
        for i in range(0, number_of_bins):
            lower_bound = "" if i == 0 else f"{bins_list[i]} <= value_"
            upper_bound = "" if i == number_of_bins - 1 else f"value_ < {bins_list[i+1]}"
            optional_and = "" if lower_bound == "" or upper_bound == "" else " AND "
            field_clauses.append(f"SUM(CASE WHEN {lower_bound}{optional_and}{upper_bound} THEN frequency_ END)")

        fields = ",\n ".join(field_clauses)

        value_frequencies_cte = self.profiling_sql_value_frequencies_cte(table_name, column_name)

        sql = dedent(
            f"""
            WITH
                {value_frequencies_cte}
            SELECT {fields}
            FROM value_frequencies"""
        )
        return sql, bins_list

    ######################
    # Query Execution
    ######################

    def get_row_counts_all_tables(
        self,
        include_tables: list[str] | None = None,
        exclude_tables: list[str] | None = None,
        query_name: str | None = None,
    ) -> dict[str, int]:
        """
        Returns a dict that maps table names to row counts.
        """
        sql = self.sql_get_table_names_with_count(include_tables=include_tables, exclude_tables=exclude_tables)
        if sql:
            query = Query(
                data_source_scan=self.data_source_scan,
                unqualified_query_name=query_name or "get_row_counts_all_tables",
                sql=sql,
            )
            query.execute()
            return {row[0]: row[1] for row in query.rows}
        # Single query to get the metadata not available, get the counts one by one.
        all_tables = self.get_table_names(include_tables=include_tables, exclude_tables=exclude_tables)
        result = {}

        for table in all_tables:
            query_name_str = f"get_row_count_{table}"
            if query_name:
                query_name_str = f"{query_name}_{table}"
            query = Query(
                data_source_scan=self.data_source_scan,
                unqualified_query_name=query_name_str,
                sql=self.sql_get_table_count(self.quote_table(table)),
            )
            query.execute()
            if query.rows:
                result[table] = query.rows[0][0]

        return result

    def get_table_names(
        self,
        filter: str | None = None,
        include_tables: list[str] = [],
        exclude_tables: list[str] = [],
        query_name: str | None = None,
    ) -> list[str]:
        sql = self.sql_find_table_names(filter, include_tables, exclude_tables)
        query = Query(
            data_source_scan=self.data_source_scan,
            unqualified_query_name=query_name or "get_table_names",
            sql=sql,
        )
        query.execute()
        table_names = [row[0] for row in query.rows]
        return table_names

    def analyze_table(self, table: str):
        if self.sql_analyze_table(table):
            Query(
                data_source_scan=self.data_source_scan,
                unqualified_query_name=f"analyze_{table}",
                sql=self.sql_analyze_table(table),
            ).execute()

    def fully_qualified_table_name(self, table_name) -> str:
        return self.prefix_table(table_name)

    def quote_table_declaration(self, table_name) -> str:
        return self.quote_table(table_name=table_name)

    def quote_table(self, table_name) -> str:
        return f'"{table_name}"'

    def prefix_table(self, table_name: str) -> str:
        if self.table_prefix:
            return f"{self.table_prefix}.{table_name}"
        return table_name

    def quote_column_declaration(self, column_name: str) -> str:
        return self.quote_column(column_name)

    def quote_column(self, column_name: str) -> str:
        return f'"{column_name}"'

    def get_sql_type_for_create_table(self, data_type: str) -> str:
        if data_type in self.SQL_TYPE_FOR_CREATE_TABLE_MAP:
            return self.SQL_TYPE_FOR_CREATE_TABLE_MAP.get(data_type)
        else:
            return data_type

    def get_sql_type_for_schema_check(self, data_type: str) -> str:
        data_source_type = self.SQL_TYPE_FOR_SCHEMA_CHECK_MAP.get(data_type)
        if data_source_type is None:
            raise NotImplementedError(
                f"Data type {data_type} is not mapped in {type(self)}.SQL_TYPE_FOR_SCHEMA_CHECK_MAP"
            )
        return data_source_type

    def literal(self, o: object):
        if o is None:
            return "NULL"
        elif isinstance(o, Number):
            return self.literal_number(o)
        elif isinstance(o, str):
            return self.literal_string(o)
        elif isinstance(o, datetime):
            return self.literal_datetime(o)
        elif isinstance(o, date):
            return self.literal_date(o)
        elif isinstance(o, list) or isinstance(o, set) or isinstance(o, tuple):
            return self.literal_list(o)
        elif isinstance(o, bool):
            return self.literal_boolean(o)
        raise RuntimeError(f"Cannot convert type {type(o)} to a SQL literal: {o}")

    def literal_number(self, value: Number):
        if value is None:
            return None
        return str(value)

    def literal_string(self, value: str):
        if value is None:
            return None
        return "'" + self.escape_string(value) + "'"

    def literal_list(self, l: list):
        if l is None:
            return None
        return "(" + (",".join([self.literal(e) for e in l])) + ")"

    def literal_date(self, date: date):
        date_string = date.strftime("%Y-%m-%d")
        return f"DATE '{date_string}'"

    def literal_datetime(self, datetime: datetime):
        return f"'{datetime.isoformat()}'"

    def literal_boolean(self, boolean: bool):
        return "TRUE" if boolean is True else "FALSE"

    def expr_count_all(self) -> str:
        return "COUNT(*)"

    def expr_count_conditional(self, condition: str):
        return f"COUNT(CASE WHEN {condition} THEN 1 END)"

    def expr_conditional(self, condition: str, expr: str):
        return f"CASE WHEN {condition} THEN {expr} END"

    def expr_count(self, expr):
        return f"COUNT({expr})"

    def expr_distinct(self, expr):
        return f"DISTINCT({expr})"

    def expr_length(self, expr):
        return f"LENGTH({expr})"

    def expr_min(self, expr):
        return f"MIN({expr})"

    def expr_max(self, expr):
        return f"MAX({expr})"

    def expr_avg(self, expr):
        return f"AVG({expr})"

    def expr_sum(self, expr):
        return f"SUM({expr})"

    def expr_regexp_like(self, expr: str, regex_pattern: str):
        return f"REGEXP_LIKE({expr}, '{regex_pattern}')"

    def expr_in(self, left: str, right: str):
        return f"{left} IN {right}"

    def cast_text_to_number(self, column_name, validity_format: str):
        """Cast string to number

        - first regex replace removes extra chars, keeps: "digits + - . ,"
        - second regex changes "," to "."
        - Nullif makes sure that if regexes return empty string then Null is returned instead
        """
        regex = self.escape_regex(r"'[^-0-9\.\,]'")
        return f"CAST(NULLIF(REGEXP_REPLACE(REGEXP_REPLACE({column_name}, {regex}, ''{self.regex_replace_flags()}), ',', '.'{self.regex_replace_flags()}), '') AS {self.SQL_TYPE_FOR_CREATE_TABLE_MAP[DataType.DECIMAL]})"

    def regex_replace_flags(self) -> str:
        return ", 'g'"

    def escape_string(self, value: str):
        return re.sub(r"(\\.)", r"\\\1", value)

    def escape_regex(self, value: str):
        return value

    def get_max_aggregation_fields(self):
        """
        Max number of fields to be aggregated in 1 aggregation query
        """
        return 50

    def connect(self, connection_properties: dict):
        """
        Subclasses use self.connection_properties to initialize self.connection with a PEP 249 connection

        Any BaseException may be raised in case of errors in the connection_properties or in case
        the database.connect itself fails for some other reason.  The caller of this method will
        catch the exception and add an error log to the scan.
        """
        raise NotImplementedError(f"TODO: Implement {type(self)}.connect()")

    def fetchall(self, sql: str):
        # TODO: Deprecated - not used, use Query object instead.
        try:
            cursor = self.connection.cursor()
            try:
                self.logs.info(f"Query: \n{sql}")
                cursor.execute(sql)
                return cursor.fetchall()
            finally:
                cursor.close()
        except BaseException as e:
            self.logs.error(f"Query error: {e}\n{sql}", exception=e)
            self.query_failed(e)

    def is_connected(self):
        return self.connection is not None

    def disconnect(self) -> None:
        if self.connection:
            self.connection.close()
            # self.connection = None is used in self.is_connected
            self.connection = None

    def commit(self):
        self.connection.commit()

    def query_failed(self, e: BaseException):
        self.rollback()

    def rollback(self):
        self.connection.rollback()

    @staticmethod
    def default_casify_table_name(identifier: str) -> str:
        """Formats table identifier to e.g. a default case for a given data source."""
        return identifier

    @staticmethod
    def default_casify_column_name(identifier: str) -> str:
        """Formats column identifier to e.g. a default case for a given data source."""
        return identifier

    @staticmethod
    def default_casify_type_name(identifier: str) -> str:
        """Formats type identifier to e.g. a default case for a given data source."""
        return identifier

    def safe_connection_data(self):
        """Return non-critically sensitive connection details.

        Useful for debugging.
        """
        # to be overridden by subclass

    def generate_hash_safe(self):
        """Generates a safe hash from non-sensitive connection details.

        Useful for debugging, identifying data sources anonymously and tracing.
        """
        data = self.safe_connection_data()

        return self.hash_data(data)

    def hash_data(self, data) -> str:
        """Hash provided data using a non-reversible hashing algorithm."""
        encoded = json.dumps(data, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()

    def test(self, sql):
        import logging
        import textwrap

        from soda.sampler.log_sampler import LogSampler
        from soda.sampler.sample_schema import SampleColumn

        cursor = self.connection.cursor()
        try:
            indented_sql = textwrap.indent(text=sql, prefix="  #   ")
            logging.debug(f"  # Query: \n{indented_sql}")
            cursor.execute(sql)
            rows = cursor.fetchall()

            columns = SampleColumn.create_sample_columns(cursor.description, self)
            table, row_count, col_count = LogSampler.pretty_print(rows, columns)
            logging.debug(f"  # Query result: \n{table}")

        except Exception as e:
            logging.error(f"Error: {e}", e)

        finally:
            cursor.close()
