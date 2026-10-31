-- Star breakdown of the reviews fetched each day, for the daily caption
-- ("1★ 14% · 2★ 5% · …"). Index 1..5 = count of 1★..5★ reviews.

alter table app_snapshots add column new_star_counts integer[]
  check (new_star_counts is null or array_length(new_star_counts, 1) = 5);
