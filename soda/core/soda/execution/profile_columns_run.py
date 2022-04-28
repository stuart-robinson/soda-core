from textwrap import dedent
from typing import TYPE_CHECKING, Dict, List

from soda.execution.profile_columns_result import ProfileColumnsResult
from soda.execution.profile_columns_result_column import ProfileColumnsResultColumn
from soda.execution.query import Query
from soda.sodacl.profile_columns_cfg import ProfileColumnsCfg

if TYPE_CHECKING:
    from soda.execution.data_source_scan import DataSourceScan


class ProfileColumnsRun:
    def __init__(self, data_source_scan: "DataSourceScan", profile_columns_cfg: ProfileColumnsCfg):

        self.data_source_scan = data_source_scan
        self.soda_cloud = data_source_scan.scan._configuration.soda_cloud
        self.data_source = data_source_scan.data_source
        self.profile_columns_cfg: ProfileColumnsCfg = profile_columns_cfg
        self.logs = self.data_source_scan.scan._logs

    def run(self) -> ProfileColumnsResult:
        profile_columns_result: ProfileColumnsResult = ProfileColumnsResult(self.profile_columns_cfg)

        # row_counts is a dict that maps table names to row counts.
        row_counts_by_table_name: Dict[str, int] = self.data_source.get_row_counts_all_tables(
            include_tables=self._get_table_expression(self.profile_columns_cfg.include_columns),
            exclude_tables=self._get_table_expression(self.profile_columns_cfg.exclude_columns),
            query_name="profile columns: get tables and row counts",
        )
        for table_name in row_counts_by_table_name:
            measured_row_count = row_counts_by_table_name[table_name]
            profile_columns_result_table = profile_columns_result.create_table(table_name, measured_row_count)

            # get columns & metadata for current table
            columns_metadata_sql = self.data_source.sql_to_get_column_metadata_for_table(table_name)
            columns_metadata_query = Query(
                data_source_scan=self.data_source_scan,
                unqualified_query_name=f"get col metadata for table: {table_name}",
                sql=columns_metadata_sql,
            )
            columns_metadata_query.execute()
            columns_metadata_result = {column[0]: column[1] for column in columns_metadata_query.rows}
            # TODO: I'd like to be able to filter columns that roll up to a numeric, text, datetime-like archetype here
            # in order to properly apply the set of profiling metrics that are compatible.
            # Ideally, I don't want to implement a mapping between db types from all dialects if we have this logic somewhere else in the
            # code

            # perform numerical metrics collection
            numerical_columns = {
                col_name: data_type
                for col_name, data_type in columns_metadata_result.items()
                if data_type in ["integer", "double precision"]
            }

            for column_name, column_type in numerical_columns.items():
                if self._is_column_included_for_profiling(column_name):
                    profile_columns_result_column: ProfileColumnsResultColumn = (
                        profile_columns_result_table.create_column(column_name, column_type)
                    )
                    value_frequencies_sql = self.sql_values_frequencies_query(table_name, column_name)

                    value_frequencies_query = Query(
                        data_source_scan=self.data_source_scan,
                        unqualified_query_name="get_profile_columns_metrics",
                        sql=value_frequencies_sql,
                    )
                    value_frequencies_query.execute()
                    if value_frequencies_query.rows is not None:
                        profile_columns_result_column.mins = [row[0] for row in value_frequencies_query.rows]
                        profile_columns_result_column.maxes = [row[1] for row in value_frequencies_query.rows]
                        profile_columns_result_column.min = profile_columns_result_column.mins[0]
                        profile_columns_result_column.max = profile_columns_result_column.maxes[0]
                        profile_columns_result_column.frequent_values = self.build_frequent_values_dict(
                            values=[row[2] for row in value_frequencies_query.rows],
                            frequencies=[row[3] for row in value_frequencies_query.rows],
                        )

                    # pure aggregates
                    aggregates_sql = self.sql_aggregates(table_name, column_name)
                    aggregates_query = Query(
                        data_source_scan=self.data_source_scan,
                        unqualified_query_name="get_pure_profiling_aggregates",
                        sql=aggregates_sql,
                    )
                    aggregates_query.execute()

                    if aggregates_query.rows is not None:
                        # the float() method isn't super good. We will want to find a way to safely get a float from a
                        # potentially dynamic result of the like Decimal(), which I don't yet if there is much of a way.
                        profile_columns_result_column.average = float(aggregates_query.rows[0][0])
                        profile_columns_result_column.sum = aggregates_query.rows[0][1]
                        profile_columns_result_column.variance = float(aggregates_query.rows[0][2])
                        profile_columns_result_column.standard_deviation = float(aggregates_query.rows[0][3])
                        profile_columns_result_column.distinct_values = int(aggregates_query.rows[0][4])
                        profile_columns_result_column.missing_values = int(aggregates_query.rows[0][5])

        return profile_columns_result

    @staticmethod
    def build_frequent_values_dict(values, frequencies):
        frequent_values = []
        for i, value in enumerate(values):
            frequent_values.append({"value": str(value), "frequency": frequencies[i]})
        return frequent_values

    def sql_values_frequencies_query(self, table_name: str, column_name: str) -> str:
        return dedent(
            f"""
                WITH values AS (
                  {self.sql_cte_value_frequencies(table_name, column_name)}
                )
                {self.sql_value_frequencies_select()}
            """
        )

    def sql_cte_value_frequencies(self, table_name: str, column_name: str) -> str:
        return dedent(
            f"""
                SELECT {column_name} as value, count(*) as frequency
                FROM {table_name}
                GROUP BY value
            """
        )

    def sql_value_frequencies_select(self) -> str:
        return dedent(
            """
            , mins as (
            select value, row_number() over(order by value asc) as idx, frequency, 'mins'::text as metric_name
            from values
            where values is not null
            order by value asc
            limit 5
        )
        , maxes as (
            select value, row_number() over(order by value desc) as idx, frequency, 'maxes'::text as metric_name
            from values
            where values is not null
            order by value desc
            limit 5
        )
        , frequent_values as (
            select
                frequency
                , row_number() over (order by frequency desc) as idx
                , value
            from values
            order by frequency desc
            limit 5
        )
        , final as (
            select
                mins.value as mins
                , maxes.value as maxes
                , frequent_values.value as frequent_values
                , frequent_values.frequency as frequency
            from mins
            join maxes
                 on mins.idx = maxes.idx
            join frequent_values
                on mins.idx = frequent_values.idx
        )
        select * from final
            """
        )

    def sql_aggregates(self, table_name: str, column_name: str) -> str:
        return dedent(
            f"""
            select
                avg({column_name}) as average
                , sum({column_name}) as sum
                , variance({column_name}) as variance
                , stddev({column_name}) as standard_deviation
                , count(distinct({column_name})) as distinct_values
                , sum(case when {column_name} is null then 1 else 0 end) as missing_values
            from {table_name}
            """
        )

    def _is_column_included_for_profiling(self, column_name):
        # TODO use string.split() to separate table expr (with wildcard) from column expr (with wildcard) using  self.profile_columns_cfg
        return True

    def get_row_counts_for_all_tables(self) -> Dict[str, int]:
        """
        Returns a dict that maps table names to row counts.
        Later this could be implemented with different queries depending on the data source type.
        """
        include_tables = []

        if len(self.profile_columns_cfg.include_columns) == 0:
            include_tables.append("%")
        else:
            include_tables.extend(self._get_table_expression(self.profile_columns_cfg.include_columns))
        include_tables.extend(self._get_table_expression(self.profile_columns_cfg.exclude_columns))
        sql = self.data_source.sql_get_table_names_with_count(include_tables=include_tables)
        query = Query(
            data_source_scan=self.data_source_scan,
            unqualified_query_name="get_counts_by_tables_for_profile_columns",
            sql=sql,
        )
        query.execute()
        return {row[0]: row[1] for row in query.rows}

    def _get_table_expression(self, columns_expression: List[str]) -> List[str]:
        table_expressions = []
        for column_expression in columns_expression:
            parts = column_expression.split(".")
            if len(parts) != 2:
                self.logs.error(
                    f'Invalid include column expression "{column_expression}"',
                    location=self.profile_columns_cfg.location,
                )
            else:
                table_expression = parts[0]
                table_expressions.append(table_expression)
        return table_expressions
