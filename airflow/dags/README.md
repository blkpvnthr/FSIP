# airflow/dags — (stub, later milestone)

Apache Airflow DAGs that orchestrate and schedule the ingestion pipeline (and the
downstream Snowflake/dbt steps) will live here in a later milestone.

**Intentionally empty in Milestone 1.** The ingestion pipeline is currently
driven by the `fsdw` CLI (see the repository `README.md`), which is the unit an
Airflow `PythonOperator`/`BashOperator` task will later call.
