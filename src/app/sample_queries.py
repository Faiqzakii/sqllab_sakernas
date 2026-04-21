from __future__ import annotations


SIMULATED_COMPLETE_DATA_SQL_TEMPLATE = (
    "SELECT art.level_1_code AS KODE_PROV, art.level_1_name AS PROV, art.level_2_code AS KODE_KAB, "
    "art.level_2_name AS KAB, art.level_3_code AS KODE_KEC, art.level_3_name AS KEC, art.level_4_code AS KODE_DESA, "
    "art.level_4_name AS DESA, art.level_5_code AS SLS, art.level_6_code AS SUBSLS, root.nks AS NKS, "
    "root.no_dsrt AS DSRT, art.ppno AS NO_ART, art.dem_name AS NAMA_ART, art.*, root.*, base.*, "
    "CONCAT('<a href=\"https://fasih-sm.bps.go.id/survey-collection/assignment-detail/', art.assignment_id, '/9b637b4c-2839-4a16-9023-1a62c364572b\" target=\"_blank\">Link Assignment</a>') AS Link, "
    "root.survey_period_id FROM tmx_1e42622b.art_roster art LEFT JOIN tmx_1e42622b.root_table root ON root.assignment_id = art.assignment_id "
    "LEFT JOIN tmx_1e42622b.base_table_assignment base ON base.id = art.assignment_id "
    "WHERE art.level_2_code='{{ level_2_code }}'"
)


SIMULATED_COMPLETE_DATA_BATCHING = {
    "type": "explicit_list",
    "param": "level_2_code",
    "values": ["01", "02", "03", "04", "71"],
}
