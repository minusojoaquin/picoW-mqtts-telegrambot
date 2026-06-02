import os
import sys
import logging
import asyncio
import ssl
import aiomysql
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from io import BytesIO
import paho.mqtt.client as mqtt
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

# IDENTIFICADOR DE HARDWARE
ID_DISPOSITIVO = "e663a837cb8d2c37" 

# EXTRACCIÓN DE ENTORNO
try:
    TB_TOKEN = os.environ["TB_TOKEN"]
    # Resolución L4 interna estricta
    MQTT_BROKER = "mosquitto"
    MQTT_PORT = 8883
    MQTT_USER = os.environ["MQTT_USR"]
    MQTT_PASS = os.environ["MQTT_PASS"]
    # Variables MariaDB
    DB_HOST = os.environ["MARIADB_SERVER"]
    DB_USER = os.environ["MARIADB_USER"]
    DB_PASS = os.environ["MARIADB_USER_PASS"]
    DB_NAME = os.environ["MARIADB_DB"]
except KeyError as e:
    logging.error(f"FATAL: Variable de entorno faltante -> {e}")
    sys.exit(1)

# CONFIGURACIÓN MQTTS
def on_connect(client, userdata, flags, reason_code, properties):
    logging.info(f"[MQTT] Handshake MQTTS completado. RC: {reason_code}")

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.on_connect = on_connect
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.tls_set(cert_reqs=ssl.CERT_NONE)
mqtt_client.tls_insecure_set(True)
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
mqtt_client.loop_start()

# CONTROLADORES DE TELEGRAM - NÚCLEO
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nombre = update.message.from_user.first_name if update.message.from_user.first_name else ""
    apellido = update.message.from_user.last_name if update.message.from_user.last_name else ""
    
    kb = [
        ["temperatura", "humedad"],
        ["gráfico temperatura", "gráfico humedad"],
        ["/modo auto", "/modo manual"],
        ["/rele 1", "/rele 0"],
        ["/destello"]
    ]
    await context.bot.send_message(
        update.message.chat.id, 
        text=f"Terminal Activa. Operador: {nombre} {apellido}\nComandos extra: /setpoint <float> | /periodo <int>",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

async def acercade(update: Update, context):
    await context.bot.send_message(update.message.chat.id, text="Terminal IoT - Subsistema MQTTS/MariaDB")

async def kill(update: Update, context):
    if context.args and context.args[0] == '@e':
        await context.bot.send_animation(update.message.chat.id, "CgACAgEAAxkBAAICI2oYKdAqh4YkBCLifiVJZlRXy74-AAKUBwACZ_PBRLgV_qZf-9kGOwQ")
        await asyncio.sleep(6)
        await context.bot.send_message(update.message.chat.id, text="Ejecución terminada.")
    else:
        await context.bot.send_message(update.message.chat.id, text="Comando bloqueado.")

# CONTROLADORES DE BASE DE DATOS (LECTURA)
async def medicion(update: Update, context):
    columna = update.message.text
    sql = f"SELECT timestamp, {columna} FROM mediciones ORDER BY timestamp DESC LIMIT 1"
    
    try:
        conn = await aiomysql.connect(host=DB_HOST, port=3306, user=DB_USER, password=DB_PASS, db=DB_NAME)
        async with conn.cursor() as cur:
            await cur.execute(sql)
            r = await cur.fetchone()
            if r:
                unidad = 'ºC' if columna == 'temperatura' else '%'
                valor = str(r[1]).replace('.',',')
                await context.bot.send_message(
                    update.message.chat.id,
                    text=f"Lectura ({columna}): {valor} {unidad} | TS: {r[0]:%H:%M:%S %d/%m/%Y}"
                )
        conn.close()
    except Exception as e:
        await context.bot.send_message(update.message.chat.id, text=f"Error I/O DB: {e}")

async def graficos(update: Update, context):
    columna = update.message.text.split()[1]
    sql = f"""SELECT timestamp, {columna}
              FROM (
                  SELECT timestamp, {columna}, ROW_NUMBER() OVER (ORDER BY id) AS rn
                  FROM mediciones
                  WHERE timestamp >= NOW() - INTERVAL 1 DAY
                  AND sensor_id LIKE 'sensor_1'
              ) AS t
              WHERE rn % 2 = 0
              ORDER BY timestamp;"""
    try:
        conn = await aiomysql.connect(host=DB_HOST, port=3306, user=DB_USER, password=DB_PASS, db=DB_NAME)
        async with conn.cursor() as cur:
            await cur.execute(sql)
            filas = await cur.fetchall()
        conn.close()

        if not filas:
            await context.bot.send_message(update.message.chat.id, text="Dataset vacío.")
            return

        fig, ax = plt.subplots(figsize=(7, 4))
        fecha, var = zip(*filas)
        ax.plot(fecha, var)
        ax.grid(True, which='both')
        ax.set_title(update.message.text, fontsize=14, verticalalignment='bottom')
        ax.set_xlabel('Timestamp')
        ax.set_ylabel('Magnitud')

        buffer = BytesIO()
        fig.tight_layout()
        fig.savefig(buffer, format='png')
        plt.close()
        buffer.seek(0)
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=buffer)
        buffer.close()
    except Exception as e:
        await context.bot.send_message(update.message.chat.id, text=f"Falla de renderizado: {e}")

# CONTROLADORES MQTT (ESCRITURA)
async def publicar_mqtt(update: Update, topic_suffix: str, payload: str):
    topic = f"{ID_DISPOSITIVO}/{topic_suffix}"
    info = mqtt_client.publish(topic, payload, qos=1)
    if info.rc == mqtt.MQTT_ERR_SUCCESS:
        await update.message.reply_text(f"Tx -> {topic} : {payload}")
    else:
        await update.message.reply_text(f"Error TX RC: {info.rc}")

async def setpoint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Sintaxis: /setpoint <float>")
    await publicar_mqtt(update, "setpoint", context.args[0])

async def periodo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Sintaxis: /periodo <int>")
    await publicar_mqtt(update, "periodo", context.args[0])

async def modo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0] not in ['auto', 'manual']:
        return await update.message.reply_text("Sintaxis: /modo auto | /modo manual")
    await publicar_mqtt(update, "modo", context.args[0])

async def rele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0] not in ['0', '1']:
        return await update.message.reply_text("Sintaxis: /rele 1 | /rele 0")
    await publicar_mqtt(update, "rele", context.args[0])

async def destello(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await publicar_mqtt(update, "destello", "1")

# BUCLE PRINCIPAL
def main():
    application = Application.builder().token(TB_TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('acercade', acercade))
    application.add_handler(CommandHandler('kill', kill))
    
    application.add_handler(CommandHandler('setpoint', setpoint))
    application.add_handler(CommandHandler('periodo', periodo))
    application.add_handler(CommandHandler('modo', modo))
    application.add_handler(CommandHandler('rele', rele))
    application.add_handler(CommandHandler('destello', destello))
    
    application.add_handler(MessageHandler(filters.Regex("^(temperatura|humedad)$"), medicion))
    application.add_handler(MessageHandler(filters.Regex("^(gráfico temperatura|gráfico humedad)$"), graficos))
    
    application.run_polling()

if __name__ == '__main__':
    main()