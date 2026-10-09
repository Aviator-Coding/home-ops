-- Weekly DuckLake maintenance. Connection details come from the PG* and AWS_* environment.
INSTALL ducklake;
INSTALL postgres;
INSTALL httpfs;

CREATE SECRET ducklake_s3 (
  TYPE s3,
  KEY_ID getenv('AWS_ACCESS_KEY_ID'),
  SECRET getenv('AWS_SECRET_ACCESS_KEY'),
  ENDPOINT getenv('S3_ENDPOINT'),
  URL_STYLE 'path',
  USE_SSL false,
  REGION 'us-east-1'
);

ATTACH 'ducklake:postgres:dbname=ducklake' AS lake (DATA_PATH 's3://ducklake/');

CALL lake.set_option('expire_older_than', '30 days');
CALL lake.set_option('delete_older_than', '3 days');

-- flush inlined data, expire snapshots, merge and rewrite files, then delete files past delete_older_than.
CHECKPOINT lake;
