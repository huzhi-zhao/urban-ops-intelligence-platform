### backfill upload

```shell
python -m scripts.backfill.backfill_dcp --start 2026-06-13 --end 2026-06-14 --bucket nyc-uoip-prod

python -m scripts.backfill.backfill_open_meteo --start 2026-06-07 --end 2026-06-14 --bucket nyc-uoip-prod 

python -m scripts.backfill.backfill_nyc_311 --start 2026-06-12 --end 2026-06-13 --bucket nyc-uoip-prod

python -m scripts.backfill.backfill_nypd \
  --start 2026-05-01 --end 2026-06-01 \
  --bucket nyc-uoip-prod --dry-run
# 2. dry-run 看着 OK 了再真传
python -m scripts.backfill.backfill_nypd \
  --start 2026-05-01 --end 2026-06-01 \
  --bucket nyc-uoip-prod

# 只拉 collisions
python -m scripts.backfill.backfill_nypd \
  --start 2026-05-01 --end 2026-06-01 \
  --bucket nyc-uoip-prod --dataset nypd_collisions

# 如果想并发拉 4 个 dataset(可能更慢,可能更快,看限速情况):

python -m scripts.backfill.backfill_nypd \
  --start 2026-05-01 --end 2026-06-01 \
  --bucket nyc-uoip-prod --max-workers 4

```
#### dry-run

⏺ --dry-run = 只跑不写。具体含义:

  - 调 upstream API(Socrata / Open-Meteo)真拉数据
  - 不打 GCS — data_*.json 和 manifest_*.json 都不写
  - 打到 log 上的东西等价于真传时:<source> <day>: <N> records DRY-RUN

  用途

  - 验证连通性:Socrata 是否 503、Open-Meteo 是否限流、token 是否有效
  - 看数据量:[2026-06-12, 2026-06-13) 一天 311 → ~400 条 records
  - 不花钱:GCS Class A 操作(写)免费但 Class B(读)不免费;dry-run 一次读 = 一次 Class B,真传一次读 = 一次 Class B + 一次 Class A



### fetch
```shell
 python -m scripts.backfill.backfill_open_meteo \
      --start 2026-06-07 --end 2026-06-14 --bucket nyc-uoip-prod --action fetch
```

