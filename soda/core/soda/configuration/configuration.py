from __future__ import annotations

from soda.common.file_system import file_system
from soda.execution.telemetry import Telemetry
from soda.sampler.default_sampler import DefaultSampler
from soda.sampler.sampler import Sampler
from soda.scan import Scan
from soda.soda_cloud.soda_cloud import SodaCloud
from soda.sodacl.format_cfg import FormatCfg


class Configuration:
    def __init__(self, scan: Scan):
        self.scan = scan
        self.connection_properties_by_name: dict[str, dict] = {}
        self.data_source_properties_by_name: dict[str, dict] = {}
        self.format_cfgs: dict[str, str] = FormatCfg.default_formats
        self.telemetry: Telemetry | None = Telemetry()
        self.soda_cloud: SodaCloud | None = None
        self.file_system = file_system()
        self.sampler: Sampler = DefaultSampler()

    def add_spark_session(self, data_source_name: str, spark_session):
        self.connection_properties_by_name["spark_df_data_source"] = {
            "type": "spark_df",
            "spark_session": spark_session,
        }
        self.data_source_properties_by_name[data_source_name] = {
            "type": "spark_df",
            "connection": "spark_df_data_source",
        }
