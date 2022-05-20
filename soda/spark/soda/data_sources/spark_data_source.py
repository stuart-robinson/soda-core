import logging
from typing import List, Optional
from soda.common.exceptions import DataSourceConnectionError

from soda.execution.data_source import DataSource

import itertools

import pyodbc
from collections import namedtuple
from enum import Enum
from pyhive import hive
from pyhive.exc import Error
from soda.execution.query import Query
from thrift.transport.TTransport import TTransportException
import logging
from typing import Any, Dict, List, Optional
from soda.common.logs import Logs
from soda.__version__ import SODA_CORE_VERSION
from soda.execution.data_type import DataType

logger = logging.getLogger(__name__)
ColumnMetadata = namedtuple("ColumnMetadata", ["name", "data_type", "is_nullable"])


def hive_connection_function(
    username: str,
    password: str,
    host: str,
    port: str,
    database: str,
    auth_method: str,
    **kwargs,
) -> hive.Connection:
    """
    Connect to hive.

    Parameters
    ----------
    username : str
        The user name
    password : str
        The password
    host: str
        The host.
    port : str
        The port
    database : str
        The databse
    auth_method : str
        The authentication method

    Returns
    -------
    out : hive.Connection
        The hive connection
    """
    connection = hive.connect(
        username=username, password=password, host=host, port=port, database=database, auth=auth_method
    )
    return connection


def _build_odbc_connnection_string(**kwargs: Any) -> str:
    return ";".join([f"{k}={v}" for k, v in kwargs.items()])


def odbc_connection_function(
    driver: str,
    host: str,
    port: str,
    token: str,
    organization: str,
    cluster: str,
    server_side_parameters: Dict[str, str],
    **kwargs,
) -> pyodbc.Connection:
    """
    Connect to hive.

    Parameters
    ----------
    driver : str
        The path to the driver
    host: str
        The host.
    port : str
        The port
    token : str
        The login token
    organization : str
        The organization
    cluster : str
        The cluster
    server_side_parameters : Dict[str]
        The server side parameters

    Returns
    -------
    out : pyobc.Connection
        The connection
    """
    http_path = f"/sql/protocolv1/o/{organization}/{cluster}"
    user_agent_entry = f"soda-sql-spark/{SODA_CORE_VERSION} (Databricks)"

    connection_str = _build_odbc_connnection_string(
        DRIVER=driver,
        HOST=host,
        PORT=port,
        UID="token",
        PWD=token,
        HTTPPath=http_path,
        AuthMech=3,
        SparkServerType=3,
        ThriftTransport=2,
        SSL=1,
        UserAgentEntry=user_agent_entry,
        LCaseSspKeyName=0 if server_side_parameters else 1,
        **server_side_parameters,
    )
    connection = pyodbc.connect(connection_str, autocommit=True)
    return connection


class SparkConnectionMethod(str, Enum):
    HIVE = "hive"
    ODBC = "odbc"


class DataSourceImpl(DataSource):
    TYPE = "spark"

    SCHEMA_CHECK_TYPES_MAPPING: Dict = {
        "string": ["character varying", "varchar"],
        "integer": ["integer", "int"],
    }
    SQL_TYPE_FOR_CREATE_TABLE_MAP: Dict = {
        DataType.TEXT: "string",
        DataType.INTEGER: "integer",
        DataType.DECIMAL: "decimal",
        DataType.DATE: "date",
        DataType.TIME: "timestamp",
        DataType.TIMESTAMP: "timestamp",
        DataType.TIMESTAMP_TZ: "timestamp",  # No timezone support in Spark
        DataType.BOOLEAN: "boolean",
    }

    SQL_TYPE_FOR_SCHEMA_CHECK_MAP = {
        DataType.TEXT: "string",
        DataType.INTEGER: "integer",
        DataType.DECIMAL: "decimal",
        DataType.DATE: "date",
        DataType.TIME: "timestamp",
        DataType.TIMESTAMP: "timestamp",
        DataType.TIMESTAMP_TZ: "timestamp",  # No timezone support in Spark
        DataType.BOOLEAN: "boolean",
    }

    def __init__(self, logs: Logs, data_source_name: str, data_source_properties: dict, connection_properties: dict):
        super().__init__(logs, data_source_name, data_source_properties, connection_properties)

        self.method = connection_properties.get("method", "hive")
        self.host = connection_properties.get("host", "localhost")
        self.port = connection_properties.get("port", "10000")
        self.username = connection_properties.get("username")
        self.password = connection_properties.get("password")
        self.database = connection_properties.get("database", "default")
        self.auth_method = connection_properties.get("authentication", None)
        self.configuration = connection_properties.get("configuration", {})
        self.driver = connection_properties.get("driver", None)
        self.token = connection_properties.get("token")
        self.organization = connection_properties.get("organization", None)
        self.cluster = connection_properties.get("cluster", None)
        self.server_side_parameters = {
            f"SSP_{k}": f"{{{v}}}" for k, v in connection_properties.get("server_side_parameters", {})
        }

    def connect(self, connection_properties):
        if self.method == SparkConnectionMethod.HIVE:
            connection_function = hive_connection_function
        elif self.method == SparkConnectionMethod.ODBC:
            connection_function = odbc_connection_function
        else:
            raise NotImplementedError(f"Unknown Spark connection method {self.method}")

        try:
            connection = connection_function(
                username=self.username,
                password=self.password,
                host=self.host,
                port=self.port,
                database=self.database,
                auth_method=self.auth_method,
                driver=self.driver,
                token=self.token,
                organization=self.organization,
                cluster=self.cluster,
                server_side_parameters=self.server_side_parameters,
            )
            return connection
        except Exception as e:
            raise DataSourceConnectionError(self.type, e)

    def sql_to_get_column_metadata_for_table(self, table_name: str):
        return (
            f"SELECT column_name, data_type, is_nullable "
            f"FROM `{self.dataset_name}.INFORMATION_SCHEMA.COLUMNS` "
            f"WHERE table_name = '{table_name}';"
        )

    def sql_get_column(
        self, include_tables: Optional[List[str]] = None, exclude_tables: Optional[List[str]] = None
    ) -> str:
        table_filter_expression = self.sql_table_include_exclude_filter(
            "table_name", "table_schema", include_tables, exclude_tables
        )
        where_clause = f"\nWHERE {table_filter_expression} \n" if table_filter_expression else ""
        return (
            f"SELECT table_name, column_name, data_type, is_nullable \n"
            f"FROM {self.dataset_name}.INFORMATION_SCHEMA.COLUMNS"
            f"{where_clause}"
        )

    def sql_find_table_names(
        self,
        filter: str | None = None,
        include_tables: list[str] = [],
        exclude_tables: list[str] = [],
        table_column_name: str = "table_name",
        schema_column_name: str = "table_schema",
    ) -> str:
        return f"SHOW TABLES FROM {self.database}"

    def sql_get_table_names_with_count(
        self, include_tables: Optional[List[str]] = None, exclude_tables: Optional[List[str]] = None
    ) -> str:
        return ""

    def get_table_names(
        self,
        filter: str | None = None,
        include_tables: list[str] = [],
        exclude_tables: list[str] = [],
        query_name: str | None = None,
    ) -> list[str]:
        sql = self.sql_find_table_names(filter, include_tables, exclude_tables)
        query = Query(
            data_source_scan=self.data_source_scan, unqualified_query_name=query_name or "get_table_names", sql=sql
        )
        query.execute()
        table_names = [row[1] for row in query.rows]

        return table_names

    def qualify_table_name(self, table_name: str) -> str:
        if self.database is None:
            qualified_table_name = table_name
        else:
            qualified_table_name = f"{self.database}.{table_name}"
        return qualified_table_name

    def rollback(self):
        # Spark does not have transactions so do nothing here.
        pass

    def sql_use_database(self) -> str:
        return f"Use {self.database}"

    # @staticmethod
    # def format_column_default(identifier: str) -> str:
    #     return identifier.lower()

    # @staticmethod
    # def format_type_default(identifier: str) -> str:
    #     return identifier.lower()

    # def safe_connection_data(self):
    #     return [
    #         self.type,
    #         self.host,
    #         self.port,
    #         self.database,
    #     ]
