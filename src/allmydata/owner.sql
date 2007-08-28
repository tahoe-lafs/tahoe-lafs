CREATE TABLE buckets
(
  bucket_id     integer PRIMARY KEY AUTOINCREMENT,
  storage_index char(32)
);

CREATE TABLE owners
(
  owner_id      integer PRIMARY KEY AUTOINCREMENT
);

CREATE TABLE leases
(
  lease_id      integer PRIMARY KEY AUTOINCREMENT,
  bucket_id     integer,
  owner_id      integer,
  renew_secret  char(32),
  cancel_secret char(32),
  expire_time   timestamp
);
