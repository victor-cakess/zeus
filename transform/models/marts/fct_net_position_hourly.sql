-- Hourly net position per balancing authority: each BA's directed flows rolled
-- up to (ba, period) — who is importing, who is exporting, and by how much.
-- Uses each BA's OWN reports only (fromba = ba): the mirrored counterparty rows
-- are that neighbor's view of the same flows, and mixing the two views would
-- double-count (M-22). Sign convention matches eia_region's TI: net_position_mwh
-- positive = net exporter for the hour.
--
-- Plain table rebuilt from fct_interchange_hourly each run (M-5/M-21) — a
-- cheap GROUP BY over the materialized fact; it never re-reads landing.
with own_reports as (
    select
        fromba as ba,
        period,
        flow_mwh
    from {{ ref('fct_interchange_hourly') }}
    where flow_mwh is not null
)

select
    ba,
    period,
    sum(case when flow_mwh > 0 then flow_mwh else 0 end) as exports_mwh,
    sum(case when flow_mwh < 0 then -flow_mwh else 0 end) as imports_mwh,
    sum(flow_mwh) as net_position_mwh
from own_reports
group by ba, period
