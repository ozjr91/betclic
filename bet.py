import os
import sqlite3
import threading
import time
import logging
from contextlib import contextmanager
from typing import List, Dict, Optional
from logging.handlers import RotatingFileHandler
import matplotlib.pyplot as plt
import requests
import schedule
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from flask import Flask, render_template
from flask_caching import Cache

# Configuration initiale
load_dotenv()
app = Flask(__name__)
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache'})

# Configuration
CONFIG = {
    "DATABASE": "tipsters.db",
    "EMAIL_USER": os.getenv("EMAIL_USER"),
    "EMAIL_PASSWORD": os.getenv("EMAIL_PASSWORD"),
    "SMTP_SERVER": "smtp.gmail.com",
    "SMTP_PORT": 465,
    "SCRAPE_URL_REMAINDER": "https://typersi.com/pozostali/remainder",
    "SCRAPE_URL_TOMORROW_TIPS": "https://typersi.com/jutro/tomorrow", # URL de la page "Tomorrow Tips" - CORRECTE MAINTENANT
    "SCRAPE_URL_BASE": "https://www.typersi.com",
    "MIN_WIN_RATE": 75,  # Seuil de win rate minimum REMIS À 75%
    "MIN_TIPS": 3        # Nombre minimum de tips
}

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            'app.log',
            maxBytes=1e6,
            backupCount=3,
            encoding='utf-8'
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def get_db_connection():
    """Fonction pour obtenir une connexion à la base de données"""
    conn = sqlite3.connect(CONFIG["DATABASE"])
    conn.row_factory = sqlite3.Row
    return conn

class TipsterScraper:
    """Classe pour le scraping des données des tipsters"""
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        })

    def fetch_tipsters_from_remainder_page(self) -> List[Dict]:
        """Récupère les tipsters depuis la page Remainder"""
        try:
            response = self.session.get(CONFIG["SCRAPE_URL_REMAINDER"], timeout=10)
            response.raise_for_status()
            return self.parse_remainder_page(response.content)
        except requests.RequestException as e:
            logger.error(f"Erreur de requête vers la page Remainder : {e}")
            return []

    def parse_remainder_page(self, html_content: bytes) -> List[Dict]:
        """Analyse la page Remainder pour extraire les tipsters et URLs de profil"""
        soup = BeautifulSoup(html_content, 'lxml')
        tipsters_data = []
        tipster_elements = soup.select('td.fw-bold')
        for element in tipster_elements:
            name_element = element.find('a', class_='link-underline-warning')
            if name_element:
                name = name_element.text.strip()
                profile_path = name_element['href']
                profile_url = CONFIG["SCRAPE_URL_BASE"] + profile_path
                tipsters_data.append({"name": name, "profile_url": profile_url})
        return tipsters_data

    def fetch_tipster_profile_data(self, profile_url: str) -> Optional[Dict]:
        """Récupère les données depuis la page de profil d'un tipster"""
        try:
            response = self.session.get(profile_url, timeout=10)
            response.raise_for_status()
            return self.parse_tipster_profile_page(response.content)
        except requests.RequestException as e:
            logger.error(f"Erreur de requête vers la page de profil : {e} - URL: {profile_url}")
            return None

    def parse_tipster_profile_page(self, html_content: bytes) -> Dict:
        """Analyse la page de profil du tipster pour extraire win rate et matchs à venir (MODIFIÉE - logging win rate amélioré)"""
        soup = BeautifulSoup(html_content, 'lxml')
        win_rate = None
        upcoming_matches = []

        # Extraction du Win Rate
        effectiveness_element = soup.select_one('div.stat div.progressC span')
        if effectiveness_element:
            win_rate_str_raw = effectiveness_element.text.strip() # Capture la valeur brute
            logger.info(f"Win rate trouvé (texte brut) : {win_rate_str_raw}") # LOG : Valeur brute
            win_rate_str = win_rate_str_raw.replace('%', '')
            logger.info(f"Win rate après replace('%', '') : {win_rate_str}") # LOG : Valeur après replace
            try:
                win_rate = float(win_rate_str)
                logger.info(f"Win rate converti en float : {win_rate}")
            except ValueError as e:
                logger.warning(f"Erreur de conversion Win rate en float : {win_rate_str} - Erreur: {e}") # LOG : Erreur détaillée
        else:
            logger.warning("Element win rate non trouvé sur la page de profil")

        # Extraction des matchs
        tips_table = soup.select_one('h2.typ.fw-bold + div.table-responsive table')
        if tips_table:
            match_rows = tips_table.select('tbody tr')
            for row in match_rows:
                match_data = [cell.text.strip() for cell in row.select('td')]
                if len(match_data) >= 9:
                    upcoming_matches.append({
                        "day": match_data[1],
                        "time": match_data[2],
                        "bookmaker": match_data[3],
                        "match": match_data[4],
                        "tip": match_data[5],
                        "stake": match_data[6],
                        "odds": match_data[7],
                        "score": match_data[8]
                    })

        return {
            "win_rate": win_rate,
            "upcoming_matches": upcoming_matches
        }

    def fetch_tips_from_tomorrow_page(self) -> List[Dict]:
        """Récupère les tips depuis la page "Tomorrow Tips" (URL corrigée)"""
        try:
            response = self.session.get(CONFIG["SCRAPE_URL_TOMORROW_TIPS"], timeout=10) # URL CORRECTE
            response.raise_for_status()
            return self.parse_tomorrow_tips(response.content)
        except requests.RequestException as e:
            logger.error(f"Erreur de requête vers la page Tomorrow Tips : {e}")
            return []

    def parse_tomorrow_tips(self, html_content: bytes) -> List[Dict]:
        """Analyse la page "Tomorrow Tips" pour extraire les tips"""
        soup = BeautifulSoup(html_content, 'lxml')
        tomorrow_tips = []

        # Sélectionner les tables de tips (les deux sections)
        tip_tables = soup.select('h2.typ.fw-bold + div.table-responsive table')  # Sélectionne toutes les tables après un h2.typ.fw-bold

        for table in tip_tables:
            match_rows = table.select('tbody tr')
            for row in match_rows:
                match_data = [cell.text.strip() for cell in row.select('td')]
                if len(match_data) >= 9:
                    tomorrow_tips.append({
                        "tipster_name": match_data[1],
                        "time": match_data[2],
                        "bookmaker": match_data[3],
                        "match": match_data[4],
                        "tip": match_data[5],
                        "odds": match_data[7],
                        "score": match_data[8]
                    })
        return tomorrow_tips

    def fetch_tipsters_from_tomorrow_tips_page(self) -> List[Dict]:
        """Récupère les tipsters depuis la page "Tomorrow Tips" """
        try:
            response = self.session.get(CONFIG["SCRAPE_URL_TOMORROW_TIPS"], timeout=10)
            response.raise_for_status()
            return self.parse_tomorrow_tipsters_page(response.content)
        except requests.RequestException as e:
            logger.error(f"Erreur de requête vers la page Tomorrow Tips (tipsters) : {e}")
            return []

    def parse_tomorrow_tips_page(self, html_content: bytes) -> List[Dict]:
        """Analyse la page "Tomorrow Tips" pour extraire les tipsters et URLs de profil"""
        soup = BeautifulSoup(html_content, 'lxml')
        tipsters_data = []
        tipster_elements = soup.select('td.fw-bold') # Assuming same selector as remainder page for tipster names
        for element in tipster_elements:
            name_element = element.find('a', class_='link-underline-warning') # Assuming same selector as remainder page for tipster links
            if name_element:
                name = name_element.text.strip()
                profile_path = name_element['href']
                profile_url = CONFIG["SCRAPE_URL_BASE"] + profile_path
                tipsters_data.append({"name": name, "profile_url": profile_url})
        return tipsters_data


class DatabaseManager:
    """Gestionnaire de base de données"""
    def __init__(self):
        self.init_db()

    def init_db(self):
        """Initialise la structure de la base de données"""
        conn = get_db_connection()
        try:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS tipsters (
                    name TEXT PRIMARY KEY,
                    win_rate REAL CHECK(win_rate BETWEEN 0 AND 100),
                    tips INTEGER CHECK(tips >= 0),
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    profile_url TEXT
                )
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_win_rate
                ON tipsters(win_rate DESC)
                ''')
            logger.info("Structure de la base de données vérifiée")
        except sqlite3.Error as e:
            logger.error(f"Erreur d'initialisation : {e}")
            raise
        finally:
            conn.close()

    def upsert_tipsters(self, tipsters: List[Dict]):
        """Mise à jour des données des tipsters"""
        conn = get_db_connection()
        try:
            conn.executemany('''
                INSERT INTO tipsters (name, win_rate, tips, profile_url)
                VALUES (:name, :win_rate, :tips, :profile_url)
                ON CONFLICT(name) DO UPDATE SET
                win_rate = excluded.win_rate,
                tips = excluded.tips,
                last_updated = CURRENT_TIMESTAMP,
                profile_url = excluded.profile_url
            ''', tipsters)
            conn.commit()
            logger.info(f"Mise à jour de {len(tipsters)} tipsters")
        except sqlite3.Error as e:
            logger.error(f"Erreur de mise à jour : {e}")
            logger.exception("Erreur détaillée lors de l'upsert (avec traceback)")
        finally:
            conn.close()

def analyze_and_visualize(tipsters: List[Dict]):
    """Génère la visualisation des données"""
    try:
        plt.figure(figsize=(10, 6))
        plt.barh(
            [t['name'] for t in tipsters],
            [t['win_rate'] for t in tipsters],
            color='#4CAF50'
        )
        plt.title('Top Tipsters - Taux de Réussite')
        plt.xlabel('Pourcentage de réussite')
        plt.tight_layout()
        plt.savefig('static/tipsters.png')
        plt.close()
        logger.info("Visualisation générée avec succès")
    except Exception as e:
        logger.error(f"Erreur de visualisation : {e}")

def scheduled_job():
    """Tâche planifiée principale (modifiée pour inclure les tips de la homepage)"""
    logger.info("Démarrage de la tâche planifiée")
    global QUALIFIED_TIPSTERS
    global TOMORROW_TIPS

    try:
        scraper = TipsterScraper()
        db = DatabaseManager()

        # Récupération des tipsters qualifiés depuis la page Remainder
        remainder_tipsters = scraper.fetch_tipsters_from_remainder_page()
        qualified_remainder_tipsters_data = [] # Renamed variable to distinguish from tomorrow tipsters
        added_tipster_names = set()

        for tipster_info in remainder_tipsters:
            profile_data = scraper.fetch_tipster_profile_data(tipster_info["profile_url"])
            if profile_data:
                win_rate = profile_data.get("win_rate")
                upcoming_matches = profile_data.get("upcoming_matches")

                if win_rate is not None and win_rate > CONFIG["MIN_WIN_RATE"]:
                    tipster_name = tipster_info["name"]
                    if tipster_name not in added_tipster_names:
                        qualified_remainder_tipsters_data.append({ # Renamed variable
                            "name": tipster_name,
                            "win_rate": win_rate,
                            "profile_url": tipster_info["profile_url"],
                            "upcoming_matches": upcoming_matches
                        })
                        added_tipster_names.add(tipster_name)

        # Récupération des tipsters de Tomorrow Tips et filtrage par win rate
        tomorrow_tipsters = scraper.fetch_tipsters_from_tomorrow_tips_page() # Fetch tipsters from tomorrow page
        qualified_tomorrow_tipsters_data = []
        for tipster_info in tomorrow_tipsters: # Iterate through tomorrow tipsters
            profile_data = scraper.fetch_tipster_profile_data(tipster_info["profile_url"])
            if profile_data:
                win_rate = profile_data.get("win_rate")
                if win_rate is not None and win_rate > CONFIG["MIN_WIN_RATE"]:
                    qualified_tomorrow_tipsters_data.append({ # Add to separate list for tomorrow tipsters
                        "name": tipster_info["name"],
                        "win_rate": win_rate,
                        "profile_url": tipster_info["profile_url"],
                        "upcoming_matches": [] # No upcoming matches fetched yet for tomorrow tipsters
                    })


        # Combine qualified tipsters from both pages (Remainder and Tomorrow Tips)
        qualified_tipsters_data = qualified_remainder_tipsters_data + qualified_tomorrow_tipsters_data

        if qualified_tipsters_data:
            analyze_and_visualize([
                {"name": t["name"], "win_rate": t["win_rate"]} for t in qualified_tipsters_data if t["win_rate"] is not None
            ])
            logger.info(f"Tipsters qualifiés trouvés : {[t['name'] for t in qualified_tipsters_data]}")
            QUALIFIED_TIPSTERS = qualified_tipsters_data
        else:
            logger.info("Aucun tipster qualifié trouvé selon les critères")
            QUALIFIED_TIPSTERS = []


        # Récupération des tips depuis la page "Tomorrow Tips" (inchangée - now uses qualified tipster list)
        tomorrow_tips = scraper.fetch_tips_from_tomorrow_page()

        # Filtrage des tips de la page "Tomorrow Tips" (inchangée - now uses qualified tipster list)
        filtered_tomorrow_tips = []
        qualified_tipster_names = {tipster['name'] for tipster in qualified_tipsters_data}
        logger.info(f"Tipsters qualifiés (noms) : {qualified_tipster_names}")

        for tip in tomorrow_tips:
            tipster_name_tip = tip["tipster_name"].strip()
            logger.info(f"Tip Tomorrow - Tipster: '{tipster_name_tip}'")

            if tipster_name_tip in qualified_tipster_names:
                filtered_tomorrow_tips.append(tip)
                logger.info(f"Tip Tomorrow - KEPT: '{tipster_name_tip}'")
            else:
                logger.info(f"Tip Tomorrow - FILTERED OUT: '{tipster_name_tip}'")

        logger.info(f"Tips de la page Tomorrow Tips récupérés : {len(tomorrow_tips)} tips, après filtrage : {len(filtered_tomorrow_tips)}")
        TOMORROW_TIPS = filtered_tomorrow_tips

    except Exception as e:
        logger.error(f"Erreur dans la tâche planifiée : {e}")

# Variables globales pour stocker les tipsters qualifiés et les tips de la homepage
QUALIFIED_TIPSTERS: List[Dict] = []
TOMORROW_TIPS: List[Dict] = []

# Configuration Flask
@app.route('/')
@cache.cached(timeout=300)
def dashboard():
    """Endpoint du tableau de bord"""
    conn = get_db_connection()
    try:
        return render_template(
            'dashboard.html',
            qualified_tipsters=QUALIFIED_TIPSTERS,
            image_path='static/tipsters.png',
            homepage_tips=TOMORROW_TIPS
        )
    except sqlite3.OperationalError as e:
        logger.critical(f"Erreur de base de données : {e}")
        return render_template('error.html', error="Base de données non initialisée"), 500
    except Exception as e:
        logger.error(f"Erreur générale : {e}")
        return render_template('error.html', error="Erreur inattendu"), 500
    finally:
        conn.close()

def run_scheduler():
    """Exécution du planificateur en arrière-plan"""
    schedule.every(3).hours.do(scheduled_job)
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == '__main__':
    os.makedirs('static', exist_ok=True)
    os.makedirs('templates', exist_ok=True)

    # Initialisation forcée de la BDD
    DatabaseManager().init_db()

    # Premier scraping immédiat
    try:
        scheduled_job()
    except Exception as e:
        logger.error(f"Erreur initiale : {e}")

    # Démarrage des threads
    threading.Thread(target=run_scheduler, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=False)