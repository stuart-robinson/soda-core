from typing import List

from soda.sodacl.location import Location


class ProfileColumnsCfg:
    def __init__(self, data_source_name: str, location: Location):
        self.data_source_name: str = data_source_name
        self.location: Location = location
        self.include_columns: List[str] = []
        self.exclude_columns: List[str] = []
        # TODO add parsing for this configuration
        self.limit_mins_maxs: int = 5
        # TODO add parsing for this configuration
        self.limit_frequent_values: int = 10
