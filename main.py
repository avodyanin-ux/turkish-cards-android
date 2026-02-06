import json
import os
import random
import time
import shutil

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.uix.floatlayout import FloatLayout


DATA_FILE_NAME = "words.json"

POOL_SIZE = 40
STREAK_TO_GRADUATE = 3
MAX_STAGE = 6

MIN_VERB_SHARE = 0.35

STAGE_COOLDOWN_DAYS = {
    1: 1,
    2: 2,
    3: 4,
    4: 7,
    5: 14,
    6: 30,
}


DEFAULT_WORDS = [
    {"tr": "ev", "ru": ["дом"], "stage": 0, "streak": 0, "due": 0, "again": 0, "correct": 0},
    {"tr": "gitmek", "ru": ["идти", "ехать", "уезжать"], "stage": 0, "streak": 0, "due": 0, "again": 0, "correct": 0},
    {"tr": "güzel", "ru": ["красивый", "хороший", "приятный"], "stage": 0, "streak": 0, "due": 0, "again": 0, "correct": 0},
    {"tr": "almak", "ru": ["брать", "покупать", "получать"], "stage": 0, "streak": 0, "due": 0, "again": 0, "correct": 0},
    {"tr": "zaman", "ru": ["время", "период"], "stage": 0, "streak": 0, "due": 0, "again": 0, "correct": 0},
]


def now() -> int:
    return int(time.time())


def day_seconds(days: int) -> int:
    return int(days) * 24 * 60 * 60


def is_infinitive_verb(tr: str) -> bool:
    tr = (tr or "").strip().lower()
    return (" " not in tr) and (tr.endswith("mek") or tr.endswith("mak"))


def complexity_score(word: dict) -> float:
    tr = str(word.get("tr", ""))
    spaces = tr.count(" ")
    length = len(tr)
    score = spaces * 10.0 + length / 10.0
    if is_infinitive_verb(tr):
        score -= 3.0
    return score


def normalize_word(w: dict) -> dict:
    w = dict(w)
    w.setdefault("tr", "")
    w.setdefault("ru", [])

    if "stage" not in w:
        interval = int(w.get("interval", 1))
        w["stage"] = min(MAX_STAGE, max(0, interval - 1))

    w.setdefault("streak", 0)
    w.setdefault("due", 0)
    w.setdefault("again", 0)
    w.setdefault("correct", int(w.get("correct", 0)))

    for k, d in [("stage", 0), ("streak", 0), ("due", 0), ("again", 0), ("correct", 0)]:
        try:
            w[k] = int(w.get(k, d))
        except Exception:
            w[k] = d

    w["stage"] = min(MAX_STAGE, max(0, w["stage"]))
    w["again"] = min(10, max(0, w["again"]))

    if isinstance(w["ru"], str):
        w["ru"] = [w["ru"]]

    return w


class StudyEngine:
    def __init__(self, data_path: str):
        self.data_path = data_path

        # 1) Если это первый запуск и в user_data_dir ещё нет words.json,
        #    пробуем подтянуть словарь из файла рядом с main.py (внутри APK он тоже будет доступен).
        if not os.path.exists(self.data_path):
            try:
                bundled_path = os.path.join(os.path.dirname(__file__), DATA_FILE_NAME)
                if os.path.exists(bundled_path):
                    shutil.copy2(bundled_path, self.data_path)
            except Exception:
                # если не получилось — просто создадим файл из DEFAULT_WORDS ниже
                pass

        self.words = self.load_words()
        self.pool = []
        self.current = None

    def load_words(self):
    if os.path.exists(self.data_path):
        with open(self.data_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [normalize_word(w) for w in data]

    # если файла нет даже после попытки копирования — создаём новый из DEFAULT_WORDS
    words = [normalize_word(w) for w in DEFAULT_WORDS]
    try:
        with open(self.data_path, "w", encoding="utf-8") as f:
            json.dump(words, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return words

    def save_words(self):
        with open(self.data_path, "w", encoding="utf-8") as f:
            json.dump(self.words, f, ensure_ascii=False, indent=2)

    def is_available_now(self, w: dict) -> bool:
        return int(w.get("due", 0)) <= now() and int(w.get("stage", 0)) < MAX_STAGE

    def refresh_pool(self):
        available = [w for w in self.words if self.is_available_now(w)]
        available.sort(key=lambda w: (int(w.get("stage", 0)), complexity_score(w)))

        verbs = [w for w in available if is_infinitive_verb(str(w.get("tr", "")))]
        others = [w for w in available if w not in verbs]

        target_verbs = int(round(POOL_SIZE * MIN_VERB_SHARE))
        target_verbs = max(0, min(target_verbs, POOL_SIZE))

        pool = []
        pool.extend(verbs[:target_verbs])
        need = POOL_SIZE - len(pool)
        pool.extend(others[:need])

        if len(pool) < POOL_SIZE:
            rest = [w for w in available if w not in pool]
            pool.extend(rest[: (POOL_SIZE - len(pool))])

        self.pool = pool

    def pick_from_pool(self):
        if not self.pool:
            return None

        weights = []
        for w in self.pool:
            stage = int(w.get("stage", 0))
            weight = 1.0 / (1.0 + stage * 1.2)

            again = int(w.get("again", 0))
            if again > 0:
                weight *= (1.0 + again * 2.5)

            if int(w.get("streak", 0)) == 0:
                weight *= 1.2

            weights.append(weight)

        return random.choices(self.pool, weights=weights, k=1)[0]

    def get_stats(self):
        learned = sum(1 for w in self.words if int(w.get("stage", 0)) >= MAX_STAGE)
        cooldown = sum(1 for w in self.words if int(w.get("stage", 0)) < MAX_STAGE and int(w.get("due", 0)) > now())
        total = len(self.words)
        return learned, cooldown, total, max(0, total - learned)

    def next_card(self):
        self.refresh_pool()
        if not self.pool:
            return None

        self.current = self.pick_from_pool()
        if self.current is None:
            return None

        if int(self.current.get("again", 0)) > 0:
            self.current["again"] = max(0, int(self.current.get("again", 0)) - 1)
            self.save_words()

        # random side (deep learning)
        if random.choice([True, False]):
            front = self.current["tr"]
            back = ", ".join(self.current["ru"])
        else:
            front = ", ".join(self.current["ru"])
            back = self.current["tr"]

        return front, back

    def answer_know(self):
        w = self.current
        if not w:
            return

        w["correct"] = int(w.get("correct", 0)) + 1
        w["streak"] = int(w.get("streak", 0)) + 1

        if int(w["streak"]) >= STREAK_TO_GRADUATE:
            w["streak"] = 0
            w["stage"] = min(MAX_STAGE, int(w.get("stage", 0)) + 1)

            if int(w["stage"]) < MAX_STAGE:
                days = int(STAGE_COOLDOWN_DAYS.get(int(w["stage"]), 2))
                w["due"] = now() + day_seconds(days)
            else:
                w["due"] = 0
        else:
            w["due"] = 0

        self.save_words()

    def answer_dont_know(self):
        w = self.current
        if not w:
            return

        w["streak"] = 0
        w["due"] = 0

        if int(w.get("stage", 0)) > 0:
            w["stage"] = int(w.get("stage", 0)) - 1

        w["again"] = min(10, int(w.get("again", 0)) + 2)

        self.save_words()


class SwipeCard(Widget):
    """Карточка: тап = reveal, свайпы после reveal: left/right = don't know/know."""

    def __init__(self, on_reveal, on_swipe_left, on_swipe_right, **kwargs):
        super().__init__(**kwargs)
        self.on_reveal = on_reveal
        self.on_swipe_left = on_swipe_left
        self.on_swipe_right = on_swipe_right
        self._touch_start = None
        self._touch_time = 0

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)

        self._touch_start = touch.pos
        self._touch_time = time.time()
        return True

    def on_touch_up(self, touch):
        if self._touch_start is None:
            return super().on_touch_up(touch)

        dx = touch.pos[0] - self._touch_start[0]
        dy = touch.pos[1] - self._touch_start[1]
        dt = time.time() - self._touch_time

        # tap (short, small move)
        if abs(dx) < dp(12) and abs(dy) < dp(12) and dt < 0.35:
            self.on_reveal()
            self._touch_start = None
            return True

        # horizontal swipe
        if abs(dx) > dp(80) and abs(dx) > abs(dy) * 1.5:
            if dx > 0:
                self.on_swipe_right()
            else:
                self.on_swipe_left()

        self._touch_start = None
        return True


class Root(FloatLayout):
    pass


class TurkishCardsApp(App):
    def build(self):
        # Это важно: words.json будем хранить в user_data_dir (внутреннее хранилище приложения)
        data_path = os.path.join(self.user_data_dir, DATA_FILE_NAME)
        self.engine = StudyEngine(data_path)

        self.front = ""
        self.back = ""
        self.revealed = False

        # UI
        root = Root()

        self.stats_lbl = Label(
            text="",
            size_hint=(1, None),
            height=dp(28),
            pos_hint={"x": 0, "top": 1},
            font_size="14sp",
            color=(0.2, 0.2, 0.2, 1),
        )
        root.add_widget(self.stats_lbl)

        # Card container
        card_box = BoxLayout(
            orientation="vertical",
            size_hint=(0.92, 0.75),
            pos_hint={"center_x": 0.5, "center_y": 0.5},
            padding=[dp(14), dp(14), dp(14), dp(14)],
            spacing=dp(10),
        )

        self.word_lbl = Label(
            text="",
            font_size="32sp",
            bold=True,
            halign="center",
            valign="middle",
            color=(0.1, 0.1, 0.1, 1),
        )
        self.word_lbl.bind(size=lambda *x: setattr(self.word_lbl, "text_size", self.word_lbl.size))

        self.progress_lbl = Label(
            text="",
            font_size="14sp",
            color=(0.4, 0.4, 0.4, 1),
            halign="center",
            valign="middle",
        )
        self.progress_lbl.bind(size=lambda *x: setattr(self.progress_lbl, "text_size", self.progress_lbl.size))

        self.trans_lbl = Label(
            text="",
            font_size="24sp",
            halign="center",
            valign="middle",
            color=(0.12, 0.12, 0.12, 1),
        )
        self.trans_lbl.bind(size=lambda *x: setattr(self.trans_lbl, "text_size", self.trans_lbl.size))

        card_box.add_widget(self.word_lbl)
        card_box.add_widget(self.progress_lbl)
        card_box.add_widget(self.trans_lbl)

        root.add_widget(card_box)

        # Invisible swipe/tap layer over the card area
        self.card_gesture = SwipeCard(
            on_reveal=self.reveal,
            on_swipe_left=self.swipe_left,
            on_swipe_right=self.swipe_right,
            size_hint=(0.92, 0.75),
            pos_hint={"center_x": 0.5, "center_y": 0.5},
        )
        root.add_widget(self.card_gesture)

        # load first card
        Clock.schedule_once(lambda *_: self.load_next(), 0)

        return root

    def update_stats(self):
        learned, cooldown, total, remaining = self.engine.get_stats()
        self.stats_lbl.text = f"Выучено: {learned} | На паузе: {cooldown} | Всего: {total} | Осталось: {remaining}"

    def load_next(self):
        self.update_stats()

        card = self.engine.next_card()
        if card is None:
            # all on cooldown or learned
            future = [int(w.get("due", 0)) for w in self.engine.words if int(w.get("stage", 0)) < MAX_STAGE and int(w.get("due", 0)) > now()]
            if future:
                next_due = min(future)
                hours = max(0, int((next_due - now()) / 3600))
                self.word_lbl.text = "Пауза"
                self.progress_lbl.text = ""
                self.trans_lbl.text = f"Сейчас нет слов.\nБлижайшее вернётся примерно через {hours} ч."
            else:
                self.word_lbl.text = "Готово"
                self.progress_lbl.text = ""
                self.trans_lbl.text = "Все слова выучены или словарь пуст."
            self.front = ""
            self.back = ""
            self.revealed = False
            return

        self.front, self.back = card
        self.revealed = False
        self.word_lbl.text = self.front
        self.trans_lbl.text = "Тапни по карточке, чтобы увидеть перевод"
        w = self.engine.current or {}
        self.progress_lbl.text = f"Этап: {int(w.get('stage',0))}/{MAX_STAGE}   |   Серия: {int(w.get('streak',0))}/{STREAK_TO_GRADUATE}"

    def reveal(self):
        if self.revealed:
            return
        if not self.front:
            return
        self.revealed = True
        self.trans_lbl.text = self.back

    def swipe_right(self):
        # Знаю (только после reveal)
        if not self.revealed:
            return
        self.engine.answer_know()
        self.load_next()

    def swipe_left(self):
        # Не знаю (только после reveal)
        if not self.revealed:
            return
        self.engine.answer_dont_know()
        self.load_next()


if __name__ == "__main__":
    TurkishCardsApp().run()
