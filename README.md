# 🐉 Kali Docker Web Manager

VirtualBox-სტილის ვებ-ინტერფეისი Kali Linux Docker სესიების მართვისთვის.  
Flask-ზე დაფუძნებული პანელი, რომელიც საშუალებას გაძლევთ შექმნათ, გაუშვათ და მართოთ მრავალი Kali Linux კონტეინერი ბრაუზერიდანვე.

---

## ✨ ფუნქციონალი

- 🖥️ **სესიების მართვა** — შექმენი, გაუშვი, შეაჩერე, გადატვირთე ან წაშალე კონტეინერები
- 👥 **Bulk (მასობრივი) შექმნა** — ერთდროულად შექმენი 1–50 სესია პრეფიქსით (მაგ. `student01`–`student50`)
- 📊 **რეალურ-დროში სტატისტიკა** — CPU, RAM, ქსელის მონიტორინგი თითოეული სესიისთვის
- 🔐 **IP Whitelist** — წვდომის შეზღუდვა IP-ის მიხედვით
- 🔑 **პაროლის შეცვლა** — სესიის პაროლის დინამიური განახლება
- ⌨️ **Exec API** — ბრძანების შესრულება კონტეინერის შიგნით
- 🐳 **Docker Image Pull** — ფონურ რეჟიმში image-ის გამოწვევა
- 🔒 **ავტორიზაცია** — სესია-ბაზირებული შესვლა

---

## 📋 მოთხოვნები

- Python 3.8+
- Docker (გაშვებული daemon-ით)
- pip პაკეტები:

```
flask
docker
```

---

## 🚀 გაშვება

### 1. კლონირება

```bash
git clone https://github.com/YOUR_USERNAME/kali-docker-manager.git
cd kali-docker-manager
```

### 2. დამოკიდებულებების დაყენება

```bash
pip install flask docker
```

### 3. (სურვილისამებრ) IP Whitelist კონფიგურაცია

შექმენი `whitelist.txt` ფაილი — თითო IP თითო სტრიქონში:

```
# დაშვებული IP-ები
192.168.1.10
10.0.0.5
```

თუ ფაილი ცარიელია ან არ არსებობს, ყველა IP-ს ექნება წვდომა.

### 4. ადმინის სერთიფიკატების შეცვლა

`app.py`-ში შეცვალე შემდეგი სტრიქონები:

```python
ADMIN_USERNAME = "administrator"   # ← შეცვალე
ADMIN_PASSWORD = "changeme"        # ← აუცილებლად შეცვალე!
```

### 5. გაშვება

```bash
python app.py
```

შემდეგ გახსენი ბრაუზერი: [http://localhost:5000](http://localhost:5000)

---

## 🗂️ პროექტის სტრუქტურა

```
kali-docker-manager/
├── app.py                  # მთავარი Flask აპლიკაცია
├── whitelist.txt           # IP whitelist (სურვილისამებრ)
├── templates/
│   ├── index.html          # მთავარი დაფა
│   ├── login.html          # შესვლის გვერდი
│   └── blocked.html        # წვდომა დახურულია
└── README.md
```

---

## 🔌 API Endpoints

| Method | Endpoint | აღწერა |
|--------|----------|--------|
| `GET` | `/api/status` | Docker სტატუსი და ინფო |
| `GET` | `/api/sessions` | ყველა სესიის სია |
| `POST` | `/api/sessions` | ახალი სესიის შექმნა |
| `POST` | `/api/sessions/<user>/start` | სესიის გაშვება |
| `POST` | `/api/sessions/<user>/stop` | სესიის შეჩერება |
| `POST` | `/api/sessions/<user>/restart` | სესიის გადატვირთვა |
| `DELETE` | `/api/sessions/<user>` | სესიის წაშლა |
| `POST` | `/api/sessions/<user>/password` | პაროლის შეცვლა |
| `POST` | `/api/sessions/<user>/exec` | ბრძანების შესრულება |
| `POST` | `/api/bulk_create` | მრავალი სესიის შექმნა |
| `POST` | `/api/pull_image` | Docker image-ის გამოწვევა |

---

## ⚙️ კონფიგურაცია

`app.py`-ში მთავარი პარამეტრები:

| პარამეტრი | ნაგულისხმევი | აღწერა |
|-----------|-------------|--------|
| `DOCKER_IMAGE` | `lscr.io/linuxserver/kali-linux:latest` | გამოსაყენებელი image |
| `PORT_START` | `6081` | პირველი პორტი სესიებისთვის |
| `CPU_LIMIT` | `8` | CPU ლიმიტი კონტეინერზე |
| `MEM_LIMIT` | `8g` | RAM ლიმიტი |
| `VNC_PASSWORD` | `kaliadmin777` | ნაგულისხმევი VNC პაროლი |

---

## ⚠️ უსაფრთხოება

> ეს პროექტი **სასწავლო/ლაბორატორიული** გამოყენებისთვისაა.

- ინტერნეტში გაშვებამდე **აუცილებლად** შეცვალე `ADMIN_PASSWORD`
- გამოიყენე HTTPS (reverse proxy — nginx/Caddy)
- IP Whitelist ჩართე საწარმოო გარემოში
- `VNC_PASSWORD` ასევე შეცვალე

---

## 📄 ლიცენზია

MIT License
