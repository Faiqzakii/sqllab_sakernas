SELECT
  r.assignment_id AS assignment_id,
  r.level_6_full_code,
  r.nama_kk,
  r.nama_usaha_bang,
  r.jumlah_usaha_prelist,
  r.jumlah_usaha_ditemukan,
  r.geotag_latitude,
  r.geotag_longitude,
  r.assignment_status_alias
FROM tgr_fd68e454.root_table r
WHERE (r.ada_bang_usaha_value ='1' OR r.ada_keluarga_value ='1')
-- no LIMIT here; runner adds keyset LIMIT 1000 per page
