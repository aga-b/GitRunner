# Operasyon rehberi

## Günlük durum kontrolü

```bash
cd /opt/GitRunner
./scripts/status.sh
docker compose logs --tail=200 controller
docker ps --filter label=personal-ci.pool=agah-ci
```

`status.json` içindeki `active`, `queued`, `draining` ve `errors` alanlarına bak.
Kuyruk varken aktif runner yoksa sırasıyla repo keşfi, label uygunluğu, GitHub
App yetkisi ve cooldown logları kontrol edilir.

## Güvenli image güncelleme

```bash
./scripts/drain.sh on
./scripts/status.sh
# active toplamı sıfır olduktan sonra
./scripts/build.sh
./scripts/preflight.sh
docker compose up -d --force-recreate controller
./scripts/drain.sh off
```

`docker system prune` kullanma. Controller yalnızca kendi label'larına sahip
nesneleri temizler; genel prune aynı hosttaki ilgisiz veriyi silebilir.

## `apt-get` bulunamadı

Bu belirti genellikle çalışan runner'ın beklenen Debian/Ubuntu image'ından
açılmadığını veya image'ın yeniden build edilmediğini gösterir.

```bash
docker run --rm --entrypoint bash personal-ci-runner:local -lc \
  'cat /etc/os-release; command -v apt-get; command -v sudo'
```

Eksikse `.env` içindeki `RUNNER_IMAGE` ile build edilen tag'in aynı olduğunu
kontrol et, `scripts/build.sh` çalıştır ve controller'ı yeniden oluştur.

## `psql` bulunamadı

```bash
docker run --rm --entrypoint psql personal-ci-runner:local --version
```

Bu repodaki Dockerfile `postgresql-client` kurar. Komut yoksa eski image
kullanılıyordur. Image build sonrası eski runner'lar kendi job'larını bitirebilir;
yeni job'un yeni container ID ve image digest ile açıldığını teyit et.

## Job sürekli queued

Workflow job'unda aşağıdaki tam label kümesi olmalıdır:

```yaml
runs-on: [self-hosted, linux, x64, ci-linux]
```

Controller sadece `ci-linux` isteyen ve izin verilen aktör/event politikasına
uyan işlere kapasite açar. `ubuntu-latest` olarak oluşmuş eski run, workflow
değişse bile rerun ile self-hosted'a taşınmaz; yeni commit/run gerekir.

## Acil durdurma

Yeni iş alımını kesmek için:

```bash
./scripts/drain.sh on
```

Bu komut mevcut job'ları kesmez. Tam bakım için aktif sayının sıfıra inmesini
bekle ve sonra controller'ı durdur. Zorla container silme yalnızca ilgili GitHub
job'unun kesileceği kabul edilerek yapılmalıdır.

## Log ve secret hijyeni

- `.env`, `secrets/`, `state/` ve PEM dosyası Git'e eklenmez.
- GitHub App private key veya PAT loglara/yazışmaya yapştırılmaz.
- Runner console logları en fazla 7 gün saklanır.
- Dashboard token'ı yalnızca secret/env ile verilir.
- Debug için shell tracing (`set -x`) JIT config veya token içeren adımlarda açılmaz.
