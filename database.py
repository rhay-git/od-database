import sqlite3
import datetime
from collections import defaultdict
from urllib.parse import urlparse
import os
import bcrypt
import uuid
import task
from crawl_server.database import TaskResult


class InvalidQueryException(Exception):
    pass


class BlacklistedWebsite:
    def __init__(self, blacklist_id, url):
        self.id = blacklist_id
        self.netloc = url


class Website:

    def __init__(self, url, logged_ip, logged_useragent, last_modified=None, website_id=None):
        self.url = url
        self.logged_ip = logged_ip
        self.logged_useragent = logged_useragent
        self.last_modified = last_modified
        self.id = website_id


class ApiToken:

    def __init__(self, token, description):
        self.token = token
        self.description = description


class Database:

    def __init__(self, db_path):

        self.db_path = db_path

        if not os.path.exists(db_path):
            self.init_database()

    def init_database(self):

        with open("init_script.sql", "r") as f:
            init_script = f.read()

        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(init_script)
            conn.commit()

    def update_website_date_if_exists(self, website_id):

        with sqlite3.connect(self.db_path) as conn:

            cursor = conn.cursor()
            cursor.execute("UPDATE Website SET last_modified=CURRENT_TIMESTAMP WHERE id=?", (website_id, ))
            conn.commit()

    def insert_website(self, website: Website):

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO Website (url, logged_ip, logged_useragent) VALUES (?,?,?)",
                           (website.url, website.logged_ip, website.logged_useragent))
            cursor.execute("SELECT LAST_INSERT_ROWID()")

            website_id = cursor.fetchone()[0]
            conn.commit()

        return website_id

    def get_website_by_url(self, url):

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT id, url, logged_ip, logged_useragent, last_modified FROM Website WHERE url=?",
                           (url, ))
            db_web = cursor.fetchone()
        if db_web:
            website = Website(db_web[1], db_web[2], db_web[3], db_web[4], db_web[0])
            return website
        else:
            return None

    def get_website_by_id(self, website_id):

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM Website WHERE id=?", (website_id, ))
            db_web = cursor.fetchone()

            if db_web:
                website = Website(db_web[1], db_web[2], db_web[3], db_web[4])
                website.id = db_web[0]
                return website
            else:
                return None

    def get_websites(self, per_page, page: int, url):
        """Get all websites"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT Website.id, Website.url, Website.last_modified FROM Website "
                           "WHERE Website.url LIKE ?"
                           "ORDER BY last_modified DESC LIMIT ? OFFSET ?", (url + "%", per_page, page * per_page))

            return cursor.fetchall()

    def get_random_website_id(self):

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM Website WHERE id >= (abs(random()) % (SELECT max(id) FROM Website)) LIMIT 1;")

            return cursor.fetchone()[0]

    def website_exists(self, url):
        """Check if an url or the parent directory of an url already exists"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT id FROM Website WHERE url = substr(?, 0, length(url) + 1)", (url, ))
            website_id = cursor.fetchone()
            return website_id[0] if website_id else None

    def get_websites_older(self, delta: datetime.timedelta):
        """Get websites last updated before a given date"""
        date = datetime.datetime.utcnow() - delta

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT Website.id FROM Website WHERE last_modified < ?", (date, ))
            return [x[0] for x in cursor.fetchall()]

    def delete_website(self, website_id):

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("DELETE FROM Website WHERE id=?", (website_id, ))
            conn.commit()

    def check_login(self, username, password) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT password FROM Admin WHERE username=?", (username, ))

            db_user = cursor.fetchone()

            if db_user:
                return bcrypt.checkpw(password.encode(), db_user[0])
            return False

    def generate_login(self, username, password) -> None:

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            hashed_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12))

            cursor.execute("INSERT INTO Admin (username, password) VALUES (?,?)", (username, hashed_pw))
            conn.commit()

    def check_api_token(self, token) -> bool:

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT token FROM ApiToken WHERE token=?", (token, ))
            return cursor.fetchone() is not None

    def generate_api_token(self, description: str) -> str:

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            token = str(uuid.uuid4())
            cursor.execute("INSERT INTO ApiToken (token, description) VALUES (?, ?)", (token, description))
            conn.commit()

            return token

    def get_tokens(self) -> list:

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM ApiToken")

            return [ApiToken(x[0], x[1]) for x in cursor.fetchall()]

    def delete_token(self, token: str) -> None:

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("DELETE FROM ApiToken WHERE token=?", (token, ))
            conn.commit()

    def get_all_websites(self) -> dict:

        # todo: mem cache that
        with sqlite3.connect(self.db_path) as conn:

            cursor = conn.cursor()

            cursor.execute("SELECT id, url FROM Website")

            result = {}

            for db_website in cursor.fetchall():
                result[db_website[0]] = db_website[1]
            return result

    def join_website_on_search_result(self, page: dict) -> dict:

        websites = self.get_all_websites()

        for hit in page["hits"]["hits"]:
            if hit["_source"]["website_id"] in websites:
                hit["_source"]["website_url"] = websites[hit["_source"]["website_id"]]
            else:
                hit["_source"]["website_url"] = "[DELETED]"

        return page

    def join_website_on_scan(self, docs: list):

        websites = self.get_all_websites()

        for doc in docs:
            if doc["_source"]["website_id"] in websites:
                doc["_source"]["website_url"] = websites[doc["_source"]["website_id"]]
            else:
                doc["_source"]["website_url"] = "[DELETED]"

            yield doc

    def join_website_on_stats(self, stats):

        websites = self.get_all_websites()

        for website in stats["website_scatter"]:
                website[0] = websites.get(website[0], "[DELETED]")

    def add_blacklist_website(self, url):

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            parsed_url = urlparse(url)
            url = parsed_url.scheme + "://" + parsed_url.netloc
            cursor.execute("INSERT INTO BlacklistedWebsite (url) VALUES (?)", (url, ))
            conn.commit()

    def remove_blacklist_website(self, blacklist_id):

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("DELETE FROM BlacklistedWebsite WHERE id=?", (blacklist_id, ))
            conn.commit()

    def is_blacklisted(self, url):

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            parsed_url = urlparse(url)
            url = parsed_url.scheme + "://" + parsed_url.netloc
            print(url)
            cursor.execute("SELECT id FROM BlacklistedWebsite WHERE url LIKE ? LIMIT 1", (url, ))

            return cursor.fetchone() is not None

    def get_blacklist(self):

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM BlacklistedWebsite")
            return [BlacklistedWebsite(r[0], r[1]) for r in cursor.fetchall()]

    def add_crawl_server(self, server: task.CrawlServer):

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("INSERT INTO CrawlServer (url, name, slots, token) VALUES (?,?,?,?)",
                           (server.url, server.name, server.slots, server.token))
            conn.commit()

    def remove_crawl_server(self, server_id):

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("DELETE FROM CrawlServer WHERE id=?", (server_id, ))
            conn.commit()

    def get_crawl_servers(self) -> list:

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT url, name, slots, token, id FROM CrawlServer")

            return [task.CrawlServer(r[0], r[1], r[2], r[3], r[4]) for r in cursor.fetchall()]

    def update_crawl_server(self, server_id, url, name, slots):

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("UPDATE CrawlServer SET url=?, name=?, slots=? WHERE id=?", (url, name, slots, server_id))
            conn.commit()

    def log_result(self, result: TaskResult):

        with sqlite3.connect(self.db_path) as conn:

            cursor = conn.cursor()

            cursor.execute("INSERT INTO TaskResult "
                           "(server, website_id, status_code, file_count, start_time, end_time) "
                           "VALUES (?,?,?,?,?,?)",
                           (result.server_id, result.website_id, result.status_code,
                            result.file_count, result.start_time, result.end_time))
            conn.commit()

    def get_crawl_logs(self):

        with sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES) as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT website_id, status_code, file_count, start_time, end_time, indexed_time, S.name "
                           "FROM TaskResult INNER JOIN CrawlServer S on TaskResult.server = S.id "
                           "ORDER BY end_time DESC")
        return [TaskResult(r[1], r[2], r[3].timestamp(), r[4].timestamp(),
                           r[0], r[5].timestamp() if r[5] else None, r[6]) for r in cursor.fetchall()]

    def get_stats_by_server(self):

        stats = dict()
        task_results = self.get_crawl_logs()

        for server in self.get_crawl_servers():
            task_count = sum(1 for result in task_results if result.server_name == server.name)
            if task_count > 0:
                stats[server.name] = dict()
                stats[server.name]["file_count"] = sum(result.file_count for result in task_results if result.server_name == server.name)
                stats[server.name]["time"] = sum((result.end_time - result.start_time) for result in task_results if result.server_name == server.name)
                stats[server.name]["task_count"] = task_count
                stats[server.name]["time_avg"] = stats[server.name]["time"] / task_count
                stats[server.name]["file_count_avg"] = stats[server.name]["file_count"] / task_count

        return stats

    def log_search(self, remote_addr, forwarded_for, q, exts, page):

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("INSERT INTO SearchLogEntry (remote_addr, forwarded_for, query, extensions, page) VALUES "
                           "(?,?,?,?,?)", (remote_addr, forwarded_for, q, ",".join(exts), page))

            conn.commit()







