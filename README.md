Proje Amacı

Bu proje; sahibinden.com ve arabam.com üzerinden günlük olarak belirli filtrelere sahip araç ilanlarını toplayarak:  Makine öğrenmesi ile fiyat değerlendirmesi yapmak

Otoendeks fiyatlarını referans almak  Sonuçları Excel formatında sunmak amacıyla geliştirilmiştir.

Hedef:

O gün yayınlanan piyasa değerine göre en uygun fiyatlı araçları listelemek.

Veri Toplama

Arabam.com

  Güvenlik seviyesi düşüktür

  toplu_ilan_cek_arabam_com.py ile toplu ve otomatik ilan çekilebilir

Sahibinden.com

  Çok katmanlı güvenlik ve örüntü algılama sistemi vardır. İlk katman olan Cloudflare ı baypass edebiliyor bot , ancak sahibinden.com örüntü tanımlama sistemi ilan_cek_sahibinden_com sınıfından oldukça insansı örüntü kullanılmasına rağme
  belli sayıda ilan çekildikten sonra (max 1000 ilan) örüntüyü tanıyor ve aynı örüntüyü birdaha kullanamıyorsunuz.

  Önerilen yöntem: Manuel toplama

sahibinden_manuel_bot klasöründeki Chrome eklentisi kullanılır 

İlanlar yeni sekmede açıldığında otomatik indirilir

Avantajları:

Ban riski yok

Botlardan daha hızlı



Bu proje, sahibinden ve arabam.com ilanları üzerinden araç fiyatlarını tahmin etmek için
istatistiksel (statik) ve makine öğrenmesi tabanlı (dinamik) yöntemleri birlikte kullanır.

Amaç; tek bir modele bağlı kalmadan, benzer ilan istatistikleri + ML ensemble ile daha dengeli ve yorumlanabilir fiyat tahmini üretmektir.

1. Statik Tahmin (Matematiksel / İstatistiksel Yaklaşım)

Statik tahmin, öğrenme içermeyen, tamamen veri içi benzerlik ve istatistik mantığına dayanır.

1.1 Aynı Temizlik – 30k KM Ortalama

Aynı Model

Aynı Yıl

KM ± 30.000

Parça bazlı boya / değişen durumu birebir aynı

En az 3 benzer ilan

Bu koşulları sağlayan ilanların aritmetik ortalama fiyatı alınır.

Çıktı:

Ayni_Temizlik_Ort_30k

1.2 Gevşek Benzerlik – 50k KM ±1 Yıl

Eğer yukarıdaki yöntem yeterli ilan bulamazsa:

Aynı Model

Yıl ±1

KM ±50.000

Bu grubun ortalaması alınır.
Bu değer sistemde baz fiyat olarak kullanılır.

Çıktı:

Benzer_Ort_50k_Yil±1

1.3 Global Medyan (Fallback)

Hiç benzer ilan bulunamazsa, referans verideki fiyat medyanı kullanılır.

1.4 Math Log-Linear Model (Parametrik Regresyon)

ML sayılmayan, klasik doğrusal regresyon kullanılır.

Kullanılan değişkenler:

Model (One-Hot)

Yıl

log(KM)

Boya sayısı

Değişen sayısı

Kritik parça boya/değişen

Çıktı:

Math_Log-Linear

Math_LogLinear_Fark

2. Dinamik Tahmin (Makine Öğrenmesi Modelleri)

Dinamik tahmin, baz fiyat + log-residual yaklaşımıyla çalışan ML modellerinden oluşur.

Kullanılan Modeller

CatBoost Regressor

LightGBM

RandomForest Regressor

Histogram Gradient Boosting Regressor (HGBR)

Her model aynı baz fiyat üzerinden fiyat sapmasını (residual) tahmin eder.

Çıktılar:

CatBoost_Tahmin

LightGBM_Tahmin

RandomForest_Tahmin

HGBR_Tahmin

**pek çok ml modeli denendi aralarında en iyi sonuçları veren  bu 4 model

3. Ensemble (Dinamik Ortalama Tahmin)

Aktif olan tüm ML modellerinin fiyat tahminleri aritmetik ortalama ile birleştirilir.

Çıktı:

Ortalama_Tahmin

4. Fark (Değerleme) Hesapları

Eğer ilanda gerçek fiyat varsa, tahmin–ilan farkları hesaplanır:


5. Excel Çıktı Yapısı

Script çalıştığında, giriş dosyasının birebir kopyası alınır ve aşağıdaki kolonlar eklenir:

Statik tahminler

ML model tahminleri

Ensemble ortalaması

Math log-linear tahmin

Dinamik / statik farklar

Bu Excel dosyası:

Filtrelenebilir

Pivot yapılabilir

Manuel fiyat analizi için doğrudan uygundur


  Genel Yaklaşım Özeti

Bu sistem:

Sadece ML’ye güvenmez

Sadece ortalamaya da kalmaz

İstatistik + ekonometrik model + ML ensemble birlikte çalışır

Amaç:

Daha stabil, daha açıklanabilir ve gerçek piyasa davranışına yakın fiyat tahmini üretmek.
örnek çıktılar için predictions2.xlsx dosyasına bakınız 
