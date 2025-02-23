from __future__ import annotations

from soda.sampler.sample_context import SampleContext
from soda.sampler.sample_ref import SampleRef
from soda.sampler.sampler import Sampler


class SodaCloudSampler(Sampler):
    def store_sample(self, sample_context: SampleContext) -> SampleRef | None:
        sample_rows = sample_context.sample.get_rows()
        row_count = len(sample_rows)
        sample_schema = sample_context.sample.get_schema()

        if row_count == 0:
            type = SampleRef.TYPE_NOT_PERSISTED
            soda_cloud_file_id = None
        else:
            type = "soda_cloud"
            scan = sample_context.scan
            soda_cloud = scan._configuration.soda_cloud
            soda_cloud_file_id = soda_cloud.upload_sample(
                scan=scan, sample_rows=sample_rows, sample_file_name=sample_context.get_sample_file_name()
            )

        return SampleRef(
            name=sample_context.sample_name,
            schema=sample_schema,
            total_row_count=row_count,
            stored_row_count=row_count,
            type=type,
            soda_cloud_file_id=soda_cloud_file_id,
        )
