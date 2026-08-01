"""
Shared Spark-submit config for all Silver DAGs (daily incremental + backfill).

Import pattern:
    from _spark_common import S3A_JARS, SPARK_CONF
"""

from __future__ import annotations

import os

# Use --jars with exact versions, not --packages.
#
# The hadoop-aws version must match the Hadoop version Spark itself bundles —
# Spark 3.5.1 ships Hadoop 3.3.4, so hadoop-aws must be 3.3.4 and the AWS SDK
# must be the 1.12.262 that hadoop-aws 3.3.4 was built against. A minor version
# apart produces java.lang.NoSuchMethodError at runtime, not a resolution error
# at submit time, so it surfaces as a mid-job crash.
#
# --packages would resolve transitive dependencies whose versions collide with
# the ones already on Spark's classpath — the same failure mode the previous
# cloud connector hit here. That reasoning survived the storage migration; only
# the coordinates changed.
HADOOP_AWS_VERSION = "3.3.4"  # must equal the Hadoop version bundled in Spark 3.5.1
AWS_SDK_VERSION = "1.12.262"  # the version hadoop-aws 3.3.4 was compiled against

S3A_JARS = ",".join([
    f"https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/"
    f"{HADOOP_AWS_VERSION}/hadoop-aws-{HADOOP_AWS_VERSION}.jar",
    f"https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/"
    f"{AWS_SDK_VERSION}/aws-java-sdk-bundle-{AWS_SDK_VERSION}.jar",
])


def s3a_endpoint() -> str:
    """MinIO endpoint for the s3a connector, from the environment."""
    return os.environ.get("S3_ENDPOINT_URL", "").strip()


SPARK_CONF = {
    "spark.hadoop.fs.s3a.impl": "org.apache.hadoop.fs.s3a.S3AFileSystem",
    "spark.hadoop.fs.s3a.endpoint": s3a_endpoint(),
    # MinIO has no virtual-host-style DNS: without path style, every bucket
    # request tries to resolve <bucket>.<host> and fails to connect.
    "spark.hadoop.fs.s3a.path.style.access": "true",
    # MinIO speaks plain HTTP on the LAN; leaving SSL on makes every call fail
    # with a handshake error rather than anything that names the cause.
    "spark.hadoop.fs.s3a.connection.ssl.enabled": "false",
    #
    # 🚨 Credentials are deliberately NOT here.
    #
    # Anything passed via --conf appears in the Spark UI's Environment page, in
    # the worker's process list, and in the Airflow task log for every run. The
    # access key and secret are injected as AWS_ACCESS_KEY_ID /
    # AWS_SECRET_ACCESS_KEY environment variables on the spark-worker container
    # (see infra/docker/docker-compose.yml), where the default credential
    # provider chain picks them up. Do not "fix" a credentials error by adding
    # spark.hadoop.fs.s3a.access.key here.
    #
    # PYTHON_VERSION_MISMATCH (worker 3.8 vs driver 3.11) fix.
    #
    # spark.executorEnv.PYSPARK_PYTHON alone does NOT work: it only injects an
    # OS env var into the Executor JVM's own process environment. The actual
    # command used to spawn the Python worker subprocess (`pythonExec`) is a
    # literal string embedded by the Driver into the serialized UDF closure
    # at build time (from spark.pyspark.python / PYSPARK_PYTHON, default
    # "python3") and shipped to the Executor as-is — the Executor's local env
    # vars never come into play. Since the Driver (airflow-scheduler) never
    # set PYSPARK_PYTHON, the embedded value defaulted to bare "python3",
    # which resolves on the spark-worker container's PATH to the base image's
    # Ubuntu Focal Python 3.8.
    #
    # spark.pyspark.python sets that embedded value correctly for the
    # Executor, but it also governs the Driver's own interpreter unless
    # overridden — and the Driver (airflow-scheduler) doesn't have
    # /usr/local/bin/python3.11 (that path only exists in the spark-worker
    # image), which broke the Driver with "Cannot run program
    # /usr/local/bin/python3.11: No such file". spark.pyspark.driver.python
    # overrides it back to a path that exists in airflow-scheduler.
    #
    # Unrelated to storage: these three survived the move to MinIO unchanged,
    # and removing them reproduces the two failures ADR 0005 records.
    #
    # Harmless for jobs with no Python UDFs (e.g. weather): these confs are
    # only consulted when a Python worker subprocess actually gets spawned.
    "spark.pyspark.python": "/usr/local/bin/python3.11",
    "spark.pyspark.driver.python": "python3",
    # Module-reference UDFs (e.g. spark/transforms/dcp.py's geojson_to_wkt_udf)
    # are cloudpickled by qualified name ("spark.transforms.dcp.<func>"), not
    # by value — the Executor's Python worker subprocess has to `import spark`
    # to resolve them at deserialize time, which fails unless the project's
    # spark/ package is on its PYTHONPATH. Paired with the spark/ volume mount
    # on the spark-worker service in infra/docker/docker-compose.yml, which
    # mounts it at the same /opt/airflow/plugins/spark path Airflow uses so
    # the qualified module name resolves identically on both sides.
    "spark.executorEnv.PYTHONPATH": "/opt/airflow/plugins",
}
