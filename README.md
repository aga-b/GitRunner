# GitRunner

GitHub Actions için kişisel hesaplara yönelik Docker runner autoscaler.

Bu paket `aga-b` hesabının erişime açtığı repoları keşfeder ve kuyruğa göre
geçici Linux x64 GitHub Actions runner'ları oluşturur. Mevcut `gitrunner`
sunucusu için varsayılan limit **20 eşzamanlı iş** (sunucu 32 çekirdek/64 GB; bellek tabanlı kabul kontrolü uygulanır); `MAX_RUNNERS` ile
değiştirilebilir. Sınır bütün repoların ortak
sınırıdır. Kuyruk boşken runner veya Docker sidecar çalışmaz; controller açık kalır.

## Hızlı başlangıç

GitHub App kimlikleri ve private key gereklidir. Docker Engine ve Compose v2
kurulu bir Linux x64 CI sunucusunda:

```bash
git clone https://github.com/aga-b/GitRunner.git
cd GitRunner
./scripts/prepare.sh
# .env içindeki App ID, Installation ID, hesap ve kapasite ayarlarını düzenle.
# App private key dosyanı secrets/github-app.pem konumuna kopyala.
./scripts/build.sh
./scripts/start.sh
./scripts/status.sh
```

Varsayılan iki iş için en az **14 GiB RAM** gerekir. 8 GiB bir sunucuda
`MAX_RUNNERS=1` kullan. Başka hesapta kurarken `GITHUB_OWNER`, `ALLOWED_ACTORS`
ve `POOL_NAME` değerlerini değiştir. Uygulama Python standart kütüphanesini
kullanır; yerel testler için pip bağımlılığı gerekmez.

Kaynak kod doğrudan repo kökündedir; ZIP açmak gerekmez. İlk yüklenen
`GitRunner.zip` özgün çalışma olarak korunmuştur. Kurulumun ayrıntıları aşağıda,
bu sürümün doğrulama sonuçları [VALIDATION.md](VALIDATION.md) içindedir.
Önceki sunucu deneyiminin notları [proje durumu](docs/PROJECT-STATUS.md) belgesindedir.

## İşleyiş

1. Controller, GitHub App üzerinden sadece hesabın kendi repolarını listeler.
2. Her 30 saniyede kuyruğa bakar; yalnız `ci-linux` etiketli, uygun aktör ve olay
   türündeki işler için kapasite açar. Yeni repolar 5 dakika içinde keşfedilir.
3. Her iş için temiz volume'lar, özel Docker ağı, Docker-in-Docker daemon ve bir
   **tek kullanımlık JIT runner** oluşturulur.
4. Runner yalnız bir iş çalıştırır. İş bittikten sonra container'lar, iş volume'ları,
   Docker ağı ve kalan runner kaydı silinir. Console/runner tanılama logları 7 gün tutulur.
5. Controller yeniden başlarsa mevcut container'ları ve kayıtları bulur. Çalışan işleri
   kapatmaz; toplam limite bunları da dahil eder.

Bir iş iki container kullanır: runner + o işe özel Docker daemon. Mevcut
`MAX_RUNNERS=2`, aynı anda **2 iş / 4 iş container'ı + 1 controller** anlamına
gelir. Daemon ve runner
aynı ağ namespace'ini ve aynı yollarla workspace/tool-cache volume'larını paylaşır.
Bu nedenle GitHub `services:` altındaki PostgreSQL'e `localhost:5432` ile erişilebilir;
başka paralel iş de aynı portu kullanabilir.

Repo düzeyindeki runner herhangi bir uyumlu işi alabilir; belirli bir job ID'ye
sabitlenmez. GitHub eşleştirmeyi yapar; controller repo kuyruğu ve boş kapasiteyi
sayarak ölçekler. GitHub hesabını organizasyona taşımak gerekmez.

## Sunucu

Debian 12/13 veya Ubuntu 24.04, Linux x64, cgroup v2, Docker Engine ve Compose v2,
Python 3 gerekir. Üretim servislerinden ayrı bir CI VM kullan.

Varsayılan sınırlar iş başına runner 4 GiB + Docker daemon 2 GiB'dir. Mevcut
host **4 vCPU (i7-9700), 15 GiB RAM ve 118 GB sistem diski** kullanır. Bu nedenle
global sınır 2'dir; iki iş için teorik container limiti 12 GiB'dir. Daha yüksek
paralellik için RAM ve CPU artırılmalıdır. Preflight gerçek RAM'i kontrol eder.
Volume/disk için ayrı bir kota uygulanmıyor; boş diski host izleme sisteminle izle.

Runner çalışırken inbound port açılmaz. GitHub API/Actions, GHCR, Docker Hub,
paket ve uygulama bağımlılık depolarına outbound erişim gerekir. Kurulum/iş paket
kaynağına göre HTTPS 443 ve HTTP 80 gerekebilir.

## 1. GitHub App oluştur

[Hesabının GitHub App ayarları](https://github.com/settings/apps) → New GitHub App.

| Alan | Değer |
|---|---|
| Ad | Hesaba özgü benzersiz bir ad; örneğin `agah-personal-ci` |
| Homepage URL | `https://github.com/aga-b` |
| Webhook | Active kapalı; bu paket polling kullanır |
| Where can this GitHub App be installed? | Only on this account |
| Repository / Administration | Read and write |
| Repository / Actions | Read-only |
| Repository / Contents | Read-only |
| Repository / Metadata | Read-only (otomatik) |

App'i `aga-b` hesabına **All repositories** seçeneğiyle kur. Bu seçim gelecekteki
repoları da erişime dahil eder. Contents yazma izni runner controller için gerekmez;
workflow geçişi kendi Git/bağlı GitHub yetkinle yapılır.

App ID'yi kaydet, private key üret ve PEM'i yalnız sunucuda sakla. Installation ID,
App kurulum ayarlarının URL'sindeki sayıdır:
`https://github.com/settings/installations/INSTALLATION_ID`.
Private key veya PAT'i sohbete yapıştırma.

Controller bu yetkiyle public repolarda `all_external_contributors` onay politikasını
uygular. Uygulayamadığı public repo için runner açmaz. Bu ayar, dış katkıcıların
workflow'larının otomatik çalışmasına engel olur; güvenilmeyen fork workflow'larını
bu CI havuzunda elle onaylama. Controller ayrıca aktörü ve PR kaynak reposunu denetler.
Workflow geçişinde iş düzeyinde aynı hesap/repo koşulu da eklenir.

## 2. Paketi aç ve ayarla

Paketi CI VM'de `/opt` altında veya seçtiğin kalıcı bir dizinde aç:

```bash
git clone https://github.com/aga-b/GitRunner.git
cd GitRunner
./scripts/prepare.sh
```

`secrets/github-app.pem` içine indirilen PEM'i kopyala; izinleri `600` yap.
`.env` içinde `GITHUB_APP_ID` ve `GITHUB_INSTALLATION_ID` değerlerini doldur.
`GITHUB_OWNER=aga-b` ve `ALLOWED_ACTORS=aga-b` hazırdır.

`INCLUDE_REPOS` boşsa App'in gördüğü bütün aktif, hesaba ait repolar keşfedilir.
Kapsamı daraltmak için `aga-b/Repo1,aga-b/Repo2` biçimini kullan.

## 3. Image build, preflight ve başlatma

```bash
./scripts/build.sh
./scripts/start.sh
./scripts/status.sh
docker compose logs --tail=100 controller
```

Build, o anda güncel resmi runner ve Docker DinD image'larını indirir; registry
digest'lerini `.env` içine yazar. App anahtarı image build context'inden dışlanır.
Preflight App erişimini, cgroup/RAM/image gereksinimlerini ve **gerçek disposable
Docker/PostgreSQL bağlantısını** test eder. Başarısızsa controller başlatılmaz.

Preflight runner yazma yetkisini bir gerçek kayıt oluşturarak test etmez; bunun
doğrulaması sonraki GitHub smoke job'ıdır. Runner kayıt servisi `runner_group_id=1`
ile repo düzeyinde JIT kaydı oluşturur. Hedef hesaptaki ilk gerçek işte API uyumluluğu
ve kayıt akışı ayrıca doğrulanmalıdır.

## 4. İlk GitHub testi

Önce bir kendi özel repouna `examples/runner-smoke.yml` dosyasını
`.github/workflows/runner-smoke.yml` olarak ekle. GitHub Actions'ta
**Self-hosted runner smoke → Run workflow** çalıştır.

Beklenen: dört shard, varsayılan kapasitede iki dalga hâlinde çalışır. En fazla
iki runner eşzamanlıdır; her PostgreSQL hizmeti kendi ortamında `5432` portunu
kullanır, dört iş tamamlanır ve bütün iş container/volume/network nesneleri temizlenir.
Controller `state/status.json` ve logları üzerinden izlenebilir. Runner'ların
bir anda listede görünmesi ya da belirli sırayla iş alması garanti değildir;
asıl ölçüt başarılı işler ve sonrasında sıfır aktif runner'dır.

Sonra aynı test iki repodan eşzamanlı çalıştırılır: toplamda en fazla `MAX_RUNNERS` (varsayılan 20) iş çalışır,
kalanlar GitHub kuyruğunda bekler. Bir controller restart testi de yapılır:
`docker compose restart controller`; çalışan işler devam etmelidir.

## 5. Mevcut workflow'ları geçir

Runner doğrulandıktan sonra repo checkout'unda değişikliği incele ve uygula:

```bash
python3 /opt/GitRunner/scripts/migrate-local.py /path/to/repo
python3 /opt/GitRunner/scripts/migrate-local.py /path/to/repo --apply
```

İlk komut diff gösterir. İkinci komut dosyaları düzenler. `ubuntu-latest` ve
`ubuntu-24.04` kullanan basit job tanımlarını şu etiketlere yönlendirir:

```yaml
runs-on: [self-hosted, linux, x64, ci-linux]
```

Job isimleri, test adımları, service ayarları ve trigger'lar korunur. Var olan job
koşulları, OS matrix'leri, Windows/macOS ve `pull_request_target` otomatik dönüştürülmez.
Bu işler ayrıca ele alınmalıdır. Sadece App'i bütün repolara kurmak `ubuntu-latest`
job'larını otomatik taşımıyor. Yeni repolarda da aynı `runs-on` etiketleri kullanılmalı.

Migration aracını her reponun güncel checkout'u üzerinde kullan. Önce diff'i
incele, sonra `--apply` ile uygula. Böylece repo içindeki yeni adımlar, job adları
ve test kapsamı korunur. Her repo kendi smoke/CI sonucuyla doğrulanır.

Quota hatası almış eski workflow run'ını yalnız rerun etmek runner seçimini değiştirmez.
Yeni workflow sürümünü commit et ve yeni bir run başlat. Branch protection/required
checks kaldırılmaz; testlerin çalışması devam eder.

## Bakım ve operasyon

```bash
./scripts/drain.sh on       # Yeni runner açmayı durdur; mevcut işler sürer
./scripts/status.sh
# Aktif iş sayısı sıfır olduktan sonra:
./scripts/build.sh          # Güncel resmi image ile yeniden build
docker compose up -d --force-recreate controller
./scripts/drain.sh off
```

Runner image'ını düzenli, tercihen haftalık yenile. GitHub runner sürümünü gerektiğinde
zorunlu tutabilir. Bu paket host üzerinde zamanlanmış update servisi kurmaz;
planlama canlı kurulumda host işletim sistemiyle birlikte yapılmalıdır. Image tag'i
değişirse mevcut işler eski image'la bitirir; yeni işler yeni image'ı kullanır.

`docker compose down` sadece controller'ı durdurur; devam eden iş container'larını
öldürmez. Tam kapatmadan önce drain ve sıfır aktif iş beklenmelidir. Genel
`docker system prune` komutları kullanılmaz; controller yalnız kendi pool etiketiyle
oluşturduğu nesneleri siler. Pool adı aynı state dizinini kullanan tek controller'a
ait olmalı. Singleton kilidi aynı state dizininde çift controller çalışmasını engeller.

Varsayılan idle timeout 10 dakika, toplam job lifetime 3 saattir. Üç saati aşan iş
container'ı durdurulur; GitHub iş durumu bağlantı kaybı/iptal olarak sonuçlanabilir.
Workflow'ta ayrıca uygun `timeout-minutes` tanımla. Log saklama varsayılanı 7 gündür;
`state/logs` üretim için merkezi log sistemine alınabilir. Loglar özel veri içerebilir;
state dizini `700` izinle hazırlanır.

## Güvenlik sınırları ve uyumluluk

- App private key ve installation token yalnız controller'dadır. Runner'a bunlar
  aktarılmaz; JIT config yalnız o runner kaydı içindir.
- Host Docker socket yalnız controller'a bağlıdır. Runner host socket'ini görmez.
- Docker sidecar `privileged` çalışır ve runner içindeki resmi `sudo` davranışı
  korunur. Ayrı Docker daemon ve container'lar bir VM güvenlik sınırının yerini
  tutmaz. Bu yüzden üretimden ayrı, sadece bu hesaba ait güvenilen CI kodunu
  çalıştıran bir VM gerekir. VM'in production subnet'lerine erişimini sınırla.
- Daemon API sadece job ağının loopback adresinde `2375` dinler; host'a port
  yayınlanmaz. Controller ve App anahtarı job ağına bağlanmaz.
- GitHub approval politikası, aktör filtresi ve job `if` koşulları birlikte kullanılır.
  Yalnız controller filtresi, mevcut boş runner'a başka bir işin atanmasını engellemez.
- Container işlerinin volume yolları runner ve daemon'da aynı tutulur. Linux cgroup v2
  gerekir. Gerçek service/container uyumluluğu GitHub smoke testiyle doğrulanır.
- Ubuntu runner image'ı GitHub-hosted `ubuntu-latest` image'ının bütün araçlarını
  içermez. Node/Python/PHP sürümleri kendi workflow setup action'larıyla kurulur.
  .NET/Java/Android gibi yeni araçlar ayrı doğrulama veya image genişletmesi ister.
- GitHub App erişim kaybı/rate limit durumunda yeni kapasite açılmaz. Hali hazırda
  çalışan işler sürer; hard lifetime geçerlidir. Cleanup API hatasında ilgili slot
  rezerve kalır, böylece toplam limit yanlışlıkla aşılmaz.

## Yerel doğrulama

```bash
./scripts/check.sh
```

Image build sonrası, GitHub runner kaydı oluşturmadan Docker/PostgreSQL
uyumluluğunu ayrıca test edebilirsin:

```bash
docker compose run --rm --no-deps controller python smoke.py
```

Davranış testleri; global/per-repo limit, adil paylaşım, idle rezervasyonu, quota azaltma,
controller restart, API kesintisi, busy/idle timeout, hard lifetime, cleanup failure,
yarım provisioning, public approval ve workflow içeriği koruma senaryolarını kapsar.
Bu testler Docker build veya gerçek GitHub execution sonucunun yerine geçmez.

## Birincil kaynaklar

- [GitHub runner kapsamı](https://docs.github.com/en/actions/concepts/runners/self-hosted-runners)
- [GitHub ephemeral runner/autoscaling referansı](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)
- [GitHub JIT runner API](https://docs.github.com/en/rest/actions/self-hosted-runners)
- [GitHub Actions permissions API](https://docs.github.com/en/rest/actions/permissions)
- [Resmi runner Dockerfile](https://github.com/actions/runner/blob/main/images/Dockerfile)
- [GitHub'un DinD runner modeli](https://docs.github.com/en/actions/how-tos/manage-runners/use-actions-runner-controller/deploy-runner-scale-sets)

Bu polling controller, GitHub Actions Runner Controller (ARC) değildir. Bu hesap için
ayrı Kubernetes kurulumu gerektirmeyen küçük bir uygulamadır; işletim ve geliştirme
sorumluluğu paketi kullanan ekibe aittir.
