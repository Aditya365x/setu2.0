"""Load test. Run it the night before and put the numbers on a slide.

Target: 500 reports/min sustained, 0 errors, p99 < 300 ms. Ingest hits those
numbers because it does almost nothing — validate, insert, enqueue, return 202.
The solver runs in the worker, so a burst never blocks a citizen's submission.
"""

import random
import uuid

from locust import HttpUser, between, task


class Reporter(HttpUser):
    wait_time = between(0.1, 0.5)

    @task
    def submit(self):
        self.client.post(
            "/api/v1/ingest/report",
            data={
                # Centred on Berhampur, spread across the district.
                "lat": 19.31 + random.gauss(0, 0.03),
                "lng": 84.79 + random.gauss(0, 0.03),
                "hazard_type": random.choice(["flood", "stranded", "medical"]),
                "severity_raw": random.randint(1, 5),
                # A realistic mix of GPS-grade and pincode-grade accuracy.
                "gps_accuracy_m": random.choice([8, 15, 3000]),
                "client_report_uuid": str(uuid.uuid4()),
            },
            name="/ingest/report",
        )
