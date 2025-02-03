import requests
from bs4 import BeautifulSoup
import sqlite3
import schedule
import time
import smtplib
from email.mime.text import MIMEText
import matplotlib.pyplot as plt
from flask import Flask, render_template
import logging

# Configuration des logs
logging.basicConfig(filename='app.log', level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Étape 1 : Fonction pour extraire les données du site Typersi
def fetch_tipsters():
    url = "https://www.typersi.com"  # Remplacez par l'URL exacte si nécessaire
    try:
        response = requests.get(url)
        response.raise_for_status()  # Lève une exception pour les codes d'erreur HTTP
        logging.info("Données récupérées avec succès.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Erreur lors de la récupération des données : {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    tipsters = []
    rows = soup.find_all('tr', class_='tipster-row')  # Classe hypothétique

    for row in rows:
        try:
            name = row.find('td', class_='name').text.strip()
            win_rate_text = row.find('td', class_='win-rate').text.strip()  # Classe hypothétique
            tips_text = row.find('td', class_='tips').text.strip()  # Classe hypothétique

            win_rate = float(win_rate_text.split('%')[0])
            tips = int(tips_text)

            tipsters.append({"name": name, "win_rate": win_rate, "tips": tips})
        except Exception as e:
            logging.error(f"Erreur lors du traitement d'une ligne : {e}")
            continue

    return tipsters

# Étape 2 : Filtrer les tipsters avec un win rate > 75% et au moins 3 paris
def filter_tipsters(tipsters):
    filtered = [t for t in tipsters if t["win_rate"] > 75 and t["tips"] >= 3]
    return filtered

# Étape 3 : Stocker les données dans une base de données SQLite
def save_to_database(filtered_tipsters):
    conn = sqlite3.connect('tipsters.db')
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tipsters (
            name TEXT,
            win_rate REAL,
            tips INTEGER,
            last_updated TEXT
        )
    ''')

    for tipster in filtered_tipsters:
        cursor.execute('''
            INSERT OR REPLACE INTO tipsters (name, win_rate, tips, last_updated)
            VALUES (?, ?, ?, datetime('now'))
        ''', (tipster['name'], tipster['win_rate'], tipster['tips']))

    conn.commit()
    conn.close()

# Étape 4 : Analyser les données avec Matplotlib
def plot_win_rates(filtered_tipsters):
    win_rates = [t['win_rate'] for t in filtered_tipsters]
    names = [t['name'] for t in filtered_tipsters]

    plt.figure(figsize=(10, 6))
    plt.barh(names, win_rates, color='skyblue')
    plt.xlabel('Win Rate (%)')
    plt.title('Tipsters avec Win Rate > 75%')
    plt.gca().invert_yaxis()  # Inverser pour afficher le meilleur en haut
    plt.show()

# Étape 5 : Envoyer des notifications par email
def send_email(filtered_tipsters):
    msg = MIMEText(str(filtered_tipsters))
    msg['Subject'] = "Nouveaux Tipsters avec Win Rate > 75%"
    msg['From'] = "votre_email@gmail.com"
    msg['To'] = "destinataire@gmail.com"

    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login("votre_email@gmail.com", "votre_mot_de_passe")
            server.sendmail(msg['From'], msg['To'], msg.as_string())
        logging.info("Email envoyé avec succès.")
    except Exception as e:
        logging.error(f"Erreur lors de l'envoi de l'email : {e}")

# Étape 6 : Planifier l'exécution automatique
def job():
    logging.info("Récupération et filtrage des données...")
    tipsters = fetch_tipsters()
    filtered_tipsters = filter_tipsters(tipsters)

    if filtered_tipsters:
        logging.info("Sauvegarde dans la base de données...")
        save_to_database(filtered_tipsters)

        logging.info("Création du graphique...")
        plot_win_rates(filtered_tipsters)

        logging.info("Envoi des notifications...")
        send_email(filtered_tipsters)
    else:
        logging.info("Aucun tipster trouvé avec un win rate > 75%.")

# Planifier l'exécution toutes les 12 heures
schedule.every(12).hours.do(job)

# Interface Flask
app = Flask(__name__)

@app.route('/')
def home():
    conn = sqlite3.connect('tipsters.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tipsters ORDER BY win_rate DESC")
    tipsters = cursor.fetchall()
    conn.close()
    return render_template('index.html', tipsters=tipsters)

if __name__ == '__main__':
    # Démarrer Flask en mode debug
    app.run(debug=True)

    # Boucle principale pour exécuter les tâches planifiées
    while True:
        schedule.run_pending()
        time.sleep(1)