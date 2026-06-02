import os
import sys
import logging
import ssl
import paho.mqtt.client as mqtt
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

# IDENTIFICADOR DE HARDWARE
ID_DISPOSITIVO = "e663a837cb8d2c37" # Se extrae al ejecutar el codigo para micropython

try:
    TB_TOKEN = os.environ["TB_TOKEN"]
    MQTT_BROKER = "mosquitto"
    MQTT_PORT = 8883
    MQTT_USER = os.environ["MQTT_USR"]
    MQTT_PASS = os.environ["MQTT_PASS"]
except KeyError as e:
    logging.error(f"FATAL: Variable de entorno faltante -> {e}")
    sys.exit(1)

def on_connect(client, userdata, flags, reason_code, properties):
    logging.info(f"[MQTT] Handshake MQTTS completado. RC: {reason_code}")

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.on_connect = on_connect
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.tls_set(cert_reqs=ssl.CERT_NONE)
mqtt_client.tls_insecure_set(True)
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
mqtt_client.loop_start()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nombre = update.message.from_user.first_name if update.message.from_user.first_name else "Operador"
    
    kb = [
        ["/modo auto", "/modo manual"],
        ["/rele 1", "/rele 0"],
        ["/destello"]
    ]
    await context.bot.send_message(
        update.message.chat.id, 
        text=f"[{nombre}]\nComandos: /setpoint | /periodo ",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

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

def main():
    application = Application.builder().token(TB_TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('setpoint', setpoint))
    application.add_handler(CommandHandler('periodo', periodo))
    application.add_handler(CommandHandler('modo', modo))
    application.add_handler(CommandHandler('rele', rele))
    application.add_handler(CommandHandler('destello', destello))
    
    application.run_polling()

if __name__ == '__main__':
    main()