from typing import Dict, List

from soda.common.exceptions import DataSourceError
from soda.execution.data_source import DataSource


class DataSourceManager:
    """
    Caches data_sources and manages connections for data_sources
    """

    def __init__(self, logs: "Logs", configuration: "Configuration"):
        self.logs = logs
        self.configuration = configuration
        self.connection_properties_by_name: Dict[str, dict] = configuration.connection_properties_by_name
        self.data_source_properties_by_name: Dict[str, dict] = configuration.data_source_properties_by_name
        self.connections: Dict[str, object] = {}
        self.data_sources: Dict[str, DataSource] = {}

    def get_data_source_names(self) -> List[str]:
        return list(self.data_source_properties_by_name.keys())

    def get_data_source(self, data_source_name: str) -> DataSource:
        """
        Returns a data_source.
        """
        data_source = self.data_sources.get(data_source_name)
        # No error generation needed as it should be checked during the parsing
        if not data_source:
            data_source_properties = self.data_source_properties_by_name.get(data_source_name)
            if data_source_properties:
                connection_name = data_source_properties.get("connection")
                if connection_name:
                    connection_properties = self.connection_properties_by_name.get(connection_name)
                    if connection_properties:
                        connection_type = data_source_properties.get("type")
                        if connection_type:
                            data_source = DataSource.create(
                                self.logs,
                                data_source_name,
                                connection_type,
                                data_source_properties,
                                connection_properties,
                            )
                            if data_source:
                                try:
                                    data_source.connect(connection_properties)
                                    self.data_sources[data_source_name] = data_source
                                except BaseException as e:
                                    self.logs.error(
                                        f'Could not connect to data source "{data_source_name}": {e}', exception=e
                                    )
                                    data_source = None
                        else:
                            self.logs.error(f'Data source "{data_source_name}" does not have a type')
            else:
                raise DataSourceError(f"Data source '{data_source_name}' not present in the configuration.")

        return data_source

    def connect(self, data_source: DataSource) -> object:
        if not data_source.connection:
            connection_name = data_source.data_source_properties.get("connection")
            data_source.connection = self._get_connection(connection_name, data_source)
        return data_source.connection

    def close_all_connections(self):
        for connection_name, connection in self.connections.items():
            try:
                connection.close()
            except BaseException as e:
                self.logs.error(f"Could not close connection {connection_name}: {e}", exception=e)

    def _get_connection(self, connection_name: str, data_source: DataSource) -> object:
        """
        Returns a connection.
        """
        connection = self.connections.get(connection_name)
        if connection is None:
            connection_properties = self.connection_properties_by_name.get(connection_name)
            return data_source.connect(connection_properties)
        return connection
