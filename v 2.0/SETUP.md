# Swing Trading Bot - Guía de Setup

## 1. Crear Bot de Telegram

1. Abre Telegram, busca **@BotFather**
2. Envía `/newbot`
3. Nombre: `Pedro Crypto Swing Bot`
4. Username: `pedro_swing_bot` (debe ser único)
5. **Copia el token** que te da BotFather
6. Envía cualquier mensaje a tu nuevo bot
7. Visita: `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
8. Busca `"chat":{"id":` → ese número es tu **chat_id**

## 2. Configurar Bybit

1. Crea cuenta en [bybit.com](https://www.bybit.com)
2. Completa KYC
3. Deposita USDT (mínimo $500)
4. Ve a **API Management** → Create New Key
5. Permisos: ✅ Read, ✅ Trade, ❌ Withdraw
6. **Copia API Key y Secret** (solo se muestran una vez)
7. Opcional: agrega IP whitelist con la IP de tu servidor

## 3. Configurar DigitalOcean

```bash
# Crear Droplet:
# - Plan: Basic $6/mes (1 vCPU, 1GB RAM, 25GB SSD)
# - Región: NYC1 o SFO3
# - OS: Ubuntu 22.04 LTS
# - Autenticación: SSH Key

# Conectar al servidor:
ssh root@<TU_IP>

# Instalar dependencias del sistema:
apt update && apt upgrade -y
apt install python3.11 python3-pip python3-venv git -y

# Crear usuario para el bot:
adduser botuser
su - botuser

# Clonar el proyecto (o subir los archivos via scp):
mkdir swing_bot && cd swing_bot
# scp -r ./swing_bot/* botuser@<TU_IP>:~/swing_bot/

# Crear entorno virtual:
python3 -m venv venv
source venv/bin/activate

# Instalar dependencias:
pip install -r requirements.txt

# Configurar variables de entorno:
cp .env.template .env
nano .env
# Llena BYBIT_API_KEY, BYBIT_API_SECRET, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
# IMPORTANTE: Deja BYBIT_TESTNET=true al inicio
```

## 4. Backtesting (ANTES de ir live)

```bash
# Descargar datos históricos (12 meses):
python fetch_data.py BTCUSDT 365
python fetch_data.py ETHUSDT 365

# Ejecutar backtest:
python backtest.py BTCUSDT_365d_240m.csv 1000
python backtest.py ETHUSDT_365d_240m.csv 1000

# Revisar resultados - debe cumplir:
# ✅ Win Rate > 55%
# ✅ Profit Factor > 1.5
# ✅ Max Drawdown < 15%
# ✅ Sharpe > 1.0
# ✅ Beats Buy & Hold
```

## 5. Modo Testnet (Paper Trading)

```bash
# En .env, asegúrate de tener:
BYBIT_TESTNET=true

# Ejecutar el bot:
python bot.py

# Observar en Telegram:
# - Recibirás mensaje de startup
# - Cada 15 min el bot escanea
# - Cada 6 horas recibes reporte de mercado
# - Prueba comandos: /status, /portfolio, /signals
```

## 6. Ir Live

```bash
# Solo después de 2+ semanas de paper trading exitoso:
# En .env, cambiar:
BYBIT_TESTNET=false

# Reiniciar bot:
sudo systemctl restart swing-bot
```

## 7. Configurar como Servicio (auto-restart)

```bash
# Crear servicio systemd:
sudo nano /etc/systemd/system/swing-bot.service
```

```ini
[Unit]
Description=Swing Trading Bot
After=network.target

[Service]
Type=simple
User=botuser
WorkingDirectory=/home/botuser/swing_bot
ExecStart=/home/botuser/swing_bot/venv/bin/python bot.py
Restart=always
RestartSec=30
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
# Activar:
sudo systemctl daemon-reload
sudo systemctl enable swing-bot
sudo systemctl start swing-bot

# Ver logs:
sudo journalctl -u swing-bot -f

# Reiniciar:
sudo systemctl restart swing-bot
```

## Comandos de Telegram

| Comando | Descripción |
|---------|-------------|
| `/status` | Estado del bot |
| `/portfolio` | Portfolio con P&L |
| `/signals` | Señales actuales |
| `/history` | Últimos 10 trades |
| `/performance` | Win rate, profit factor |
| `/zones` | Soportes y resistencias |
| `/pause` | Pausar bot |
| `/resume` | Reanudar |
| `/config` | Ver configuración |
| `/force_sell BTCUSDT` | Venta forzada |
| `/report` | Reporte bajo demanda |
