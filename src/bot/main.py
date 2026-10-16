"""
Job Hunter — Multi-User Telegram Bot (full rewrite for the multi-tenant
architecture: admin-controlled job taxonomy, hierarchical region picker,
per-user profiles, invite system, and expanded admin panel).

REQUIRES `python-telegram-bot` (>=21) — install in your own environment:
    pip install python-telegram-bot --break-system-packages

Cannot run in the sandbox that generated it (no network). Syntax-checked via
py_compile; every underlying module it calls (taxonomy, location_tree,
matching_engine, user_profiles, invites, reprocessor, backup, approval,
pipeline_multiuser, ...) is separately unit-tested — see tests/.

ARCHITECTURE NOTES:
  - No global GREEN/YELLOW/RED decision anymore. A job is either classified
    (matches a taxonomy category + at least one detected region) or it
    isn't; relevance is always computed per-user by the matching engine.
  - Karkh is NOT excluded anywhere in this file — region acceptability is
    100% a per-user preference (src/location_tree.py + src/matching_engine.py).
  - Conversation state for multi-step flows (registration, admin add/move
    node, etc.) is kept in `context.user_data` — ephemeral per process run.
    For production durability across restarts, add PicklePersistence
    (see python-telegram-bot docs) — not wired here to keep this file
    dependency-minimal beyond the core library.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

from src.ad_splitter import split_ads
from src.backup import generate_full_backup, restore_from_db
from src.config import Settings
from src.contact_links import build_whatsapp_link
from src.cv_manager import add_cv, get_active_cv_path, list_cvs
from src.database import Database
from src.invites import AccessDecision, InviteManager
from src.linkedin import is_valid_linkedin_job_url, mark_linkedin_applied, submit_manual_linkedin_url
from src import location_admin
from src.location_tree import DEFAULT_TREE_PATH, LocationTree
from src.logging_config import bot_logger as logger
from src.message_templates import generate_template
from src.notification_queue import deliver_with_rate_limit
from src.pipeline_multiuser import ingest_ad
from src.reprocessor import reprocess_archive
from src.source_tester import check_source
from src.taxonomy import TaxonomyStore
from src.telegram_utils import send_long_text
from src.user_profiles import UserProfileStore, is_valid_email

settings = Settings.load()
db = Database(settings.database_path)
taxonomy = TaxonomyStore(db)
profiles = UserProfileStore(db)
invites = InviteManager(db)
tree = LocationTree()

BOT_USERNAME_CACHE = {"value": None}


# ============================== ACCESS CONTROL ================================

def is_admin(telegram_user_id: str) -> bool:
    if telegram_user_id == settings.owner_id:
        return True
    row = db.get_user(telegram_user_id)
    return bool(row and row["is_admin"])


def admin_only(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = str(update.effective_user.id) if update.effective_user else None
        if not uid or not is_admin(uid):
            if update.effective_message:
                await update.effective_message.reply_text("🚫 هذا القسم للأدمن فقط.")
            return
        return await handler(update, context)
    return wrapper


def registered_only(handler):
    """Any registered user (not just admin) can use these — but must exist in `users`."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = str(update.effective_user.id) if update.effective_user else None
        if not uid or db.get_user(uid) is None:
            if update.effective_message:
                await update.effective_message.reply_text("أرسل /start أولاً للتسجيل.")
            return
        return await handler(update, context)
    return wrapper


# ============================== /start + REGISTRATION =========================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    existing = db.get_user(uid)
    payload = context.args[0] if context.args else None

    decision = invites.evaluate_access(uid, is_known_user=existing is not None, start_payload=payload)

    if decision == AccessDecision.INVALID_INVITE:
        await update.effective_message.reply_text("⚠️ رابط الدعوة غير صالح أو منتهي.")
        return
    if decision == AccessDecision.PENDING_APPROVAL:
        await update.effective_message.reply_text(
            "⏳ التسجيل مفتوح بالدعوة فقط حالياً. تم إرسال طلبك للأدمن — راح تنوصل رسالة عند الموافقة."
        )
        if settings.owner_id:
            await context.bot.send_message(
                chat_id=int(settings.owner_id),
                text=f"👤 طلب تسجيل جديد من المستخدم {uid}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ قبول", callback_data=f"admin:approve_reg:{uid}"),
                    InlineKeyboardButton("❌ رفض", callback_data=f"admin:reject_reg:{uid}"),
                ]]),
            )
        return

    profile = profiles.get_or_create(uid)
    if existing is None and settings.owner_id and uid == settings.owner_id:
        profiles.set_admin(uid, True)

    if not profile.is_complete:
        await update.effective_message.reply_text(
            "🤖 أهلاً بيك بـ Job Hunter!\nخلينا نسجلك — أول شي اختار الفئات الوظيفية اللي تهمك:"
        )
        await send_category_picker(update.effective_message, uid, context)
        return

    await send_main_menu(update.effective_message, uid)


async def send_main_menu(message, uid: str):
    rows = [
        [InlineKeyboardButton("🆕 وظائف تطابقني", callback_data="user:new_jobs:0")],
        [InlineKeyboardButton("👤 حسابي", callback_data="user:account"),
         InlineKeyboardButton("⭐ المحفوظة", callback_data="user:saved:0")],
        [InlineKeyboardButton("📤 تقديماتي", callback_data="user:applications:0"),
         InlineKeyboardButton("🚫 شركات أتجاهلها", callback_data="user:my_blacklist")],
        [InlineKeyboardButton("📊 إحصائياتي", callback_data="user:stats"),
         InlineKeyboardButton("🆘 مساعدة", callback_data="user:help")],
    ]
    if is_admin(uid):
        rows.append([InlineKeyboardButton("🛠 لوحة التحكم (أدمن)", callback_data="admin:menu")])
    await message.reply_text("🏠 القائمة الرئيسية", reply_markup=InlineKeyboardMarkup(rows))


# --- Category picker (flat multi-select) ---

def category_picker_keyboard(selected_ids: set, done_callback: str) -> InlineKeyboardMarkup:
    categories = taxonomy.list_categories()
    rows = []
    for cat in categories:
        mark = "☑️" if cat.id in selected_ids else "⬜"
        rows.append([InlineKeyboardButton(f"{mark} {cat.canonical_name}", callback_data=f"pick:cat:{cat.id}")])
    if not categories:
        rows.append([InlineKeyboardButton("(لا توجد فئات بعد — انتظر الأدمن يضيفها)", callback_data="noop")])
    rows.append([InlineKeyboardButton("✅ تم", callback_data=done_callback)])
    return InlineKeyboardMarkup(rows)


async def send_category_picker(message, uid: str, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reg_selected_categories"] = set(profiles.get_or_create(uid).job_category_ids)
    await message.reply_text(
        "🎯 اختر فئة وحدة أو أكثر (اضغط لتحديد/إلغاء):",
        reply_markup=category_picker_keyboard(context.user_data["reg_selected_categories"], "reg:cat:done"),
    )


# --- Area picker (hierarchical, multi-select) ---

def area_picker_keyboard(cursor_id, selected_ids: set, mode: str = "reg") -> InlineKeyboardMarkup:
    children = tree.children(cursor_id)
    rows = []
    for node in children:
        mark = "☑️" if node.id in selected_ids else "⬜"
        has_kids = bool(tree.children(node.id))
        row = [InlineKeyboardButton(f"{mark} {node.name_ar}", callback_data=f"pick:area:{node.id}")]
        if has_kids:
            row.append(InlineKeyboardButton("🔽 فتح", callback_data=f"area_nav:open:{node.id}"))
        rows.append(row)

    nav_row = []
    if cursor_id is not None:
        parent = tree.get(cursor_id)
        up_target = parent.parent_id if parent else None
        nav_row.append(InlineKeyboardButton("⬆️ للأعلى", callback_data=f"area_nav:open:{up_target or 'root'}"))
    nav_row.append(InlineKeyboardButton("✅ تم", callback_data=f"{mode}:area:done"))
    rows.append(nav_row)
    return InlineKeyboardMarkup(rows)


async def send_area_picker(message, uid: str, context: ContextTypes.DEFAULT_TYPE, mode: str = "reg"):
    context.user_data.setdefault("reg_selected_areas", set(profiles.get_or_create(uid).area_node_ids))
    context.user_data["area_cursor"] = None
    await message.reply_text(
        "📍 اختر مناطقك — تقدر تختار جانب كامل (مثلاً الرصافة كلها) أو تدخل لمناطق أدق:",
        reply_markup=area_picker_keyboard(None, context.user_data["reg_selected_areas"], mode),
    )


# ============================== JOB CARD (multi-user) ==========================

def render_job_card(row, matched_areas: set = None, uid: str = None) -> str:
    """Enhanced job card per user spec - shows all available fields."""
    category_name = "غير مصنفة"
    if row["category_id"]:
        cats = {c.id: c.canonical_name for c in taxonomy.list_categories()}
        category_name = cats.get(row["category_id"], "غير معروفة")

    lines = ["🟢 MATCHED JOB", "", f"💼 {category_name}"]
    
    # Company
    if row['company_name']:
        lines.append(f"🏢 الشركة: {row['company_name']}")
    
    # Location
    area_ids = json.loads(row["detected_area_ids"] or "[]")
    area_names = [tree.get(a).name_ar for a in area_ids if tree.get(a)]
    city = row.get("city") or "بغداد"
    if area_names:
        lines.append(f"📍 الموقع: {city} — {', '.join(area_names)}")
    elif row.get("side"):
        lines.append(f"📍 الموقع: {city} — {row['side']}")
    
    # Requirements (from raw text excerpt - not truncated silently)
    # Salary
    if row["salary_min"] or row["salary_max"]:
        lines.append(f"💰 الراتب: {row['salary_min'] or '؟'}-{row['salary_max'] or '؟'} {row['currency'] or ''}")
    
    # Work hours
    if row.get("work_hours"):
        lines.append(f"🕒 الدوام: {row['work_hours']}")
    
    # Posted date
    if row.get("source_date"):
        lines.append(f"📅 تاريخ الإعلان: {row['source_date'][:10]}")
    
    # Contact - emails (full, validated)
    emails = json.loads(row["emails"] or "[]")
    valid_emails = [e for e in emails if '@' in e and '.' in e.split('@')[-1] and len(e) > 5]
    if valid_emails:
        lines.append(f"📧 البريد: {valid_emails[0]}")
    
    # Phones (full, validated)
    phones = json.loads(row["phones"] or row["whatsapp"] or "[]")
    valid_phones = [p for p in phones if len(''.join(c for c in p if c.isdigit())) >= 7]
    if valid_phones:
        lines.append(f"📞 الهاتف: {valid_phones[0]}")
    
    # Match score
    if uid:
        try:
            strength = db.get_delivery_match_strength(row["id"], uid)
            if strength:
                lines.append(f"🎯 Match Score: {strength}%")
        except:
            pass
    
    # Source
    if row.get("source_channel"):
        lines.append(f"🔗 المصدر: {row['source_channel']}")
    if row.get("source_message_url"):
        lines.append(f"   {row['source_message_url']}")
    
    lines.append(f"\n#{row['id']}")
    return "\n".join(lines)


def job_contact_keyboard(row, job_id) -> InlineKeyboardMarkup:
    rows = []
    emails = json.loads(row["emails"] or "[]")
    whatsapp = json.loads(row["whatsapp"] or "[]")
    telegram_handles = json.loads(row["telegram_handles"] or "[]")

    contact_row = []
    if emails:
        contact_row.append(InlineKeyboardButton("✉️ تقديم فوري بالإيميل", callback_data=f"job:apply:{job_id}"))
    if whatsapp:
        link = build_whatsapp_link(whatsapp[0], prefilled_text="مرحباً، أرغب بالتقديم على الوظيفة المعلنة")
        if link:
            contact_row.append(InlineKeyboardButton("📱 تقديم عبر واتساب", url=link))
    if contact_row:
        rows.append(contact_row)

    if telegram_handles:
        rows.append([InlineKeyboardButton(f"💬 تقديم عبر تلگرام (@{telegram_handles[0]})",
                                           url=f"https://t.me/{telegram_handles[0]}")])

    rows.append([InlineKeyboardButton("📄 النص الكامل", callback_data=f"job:full:{job_id}"),
                 InlineKeyboardButton("⭐ حفظ", callback_data=f"job:save:{job_id}")])
    rows.append([InlineKeyboardButton("❌ تجاهل", callback_data=f"job:ignore:{job_id}"),
                 InlineKeyboardButton("🚫 تجاهل الشركة (لي فقط)", callback_data=f"job:ignore_company:{job_id}")])
    return InlineKeyboardMarkup(rows)


# ============================== MANUAL FORWARD INTAKE ==========================

@registered_only
async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)

    # Registration / account text inputs take priority if we're mid-flow:
    if context.user_data.get("awaiting_email"):
        await handle_email_input(update, context, uid)
        return
    if context.user_data.get("awaiting_smtp_email"):
        await handle_smtp_email_input(update, context, uid)
        return
    if context.user_data.get("awaiting_smtp_host"):
        await handle_smtp_host_input(update, context, uid)
        return
    if context.user_data.get("awaiting_smtp_password"):
        await handle_smtp_password_input(update, context, uid)
        return

    message = update.effective_message
    raw_text = message.text or message.caption
    if not raw_text:
        return

    stripped = raw_text.strip()
    if stripped.lower().startswith("http") and "linkedin.com" in stripped.lower():
        first_line, _, rest = stripped.partition("\n")
        await handle_linkedin_url(message, first_line.strip(), pipeline_context=rest.strip() or None)
        return

    if not is_admin(uid):
        await message.reply_text("ℹ️ فقط الأدمن يقدر يرسل إعلانات للتحليل مباشرة حالياً.")
        return

    ads = split_ads(raw_text)
    if len(ads) > 1:
        await message.reply_text(f"📦 وجدت {len(ads)} إعلانات — سأحلل كل واحد على حدة:")

    for ad_text in ads:
        await ingest_and_report(message, ad_text, {
            "platform": "manual", "channel": None,
            "message_id": str(message.message_id),
            "url": None, "date": message.date.isoformat() if message.date else None,
        }, context)


async def ingest_and_report(message, raw_text: str, source: dict, context: ContextTypes.DEFAULT_TYPE):
    result = ingest_ad(db, raw_text, source)  # no notify_fn — we rate-limit the batch ourselves below

    if result.duplicate_of:
        await message.reply_text(f"⚠️ إعلان مكرر (يطابق #{result.duplicate_of}) — تم تجاهله.")
        return
    if not result.category_matched:
        await message.reply_text(
            f"📋 إعلان جديد بعنوان غير معروف — أضفته لقائمة المراجعة (#{result.pending_title_id})."
        )
        return

    if result.delivered_to:
        row = db.get_job(result.job_id)

        async def send_one(target_uid: str):
            await context.bot.send_message(
                chat_id=int(target_uid), text="🔔 وظيفة جديدة تطابقك\n\n" + render_job_card(row, uid=target_uid),
                reply_markup=job_contact_keyboard(row, result.job_id),
            )
            db.mark_delivery_sent(result.job_id, target_uid)

        outcome = await deliver_with_rate_limit(result.delivered_to, send_one)
        if outcome.failed:
            for uid_, err in outcome.failed:
                logger.warning("Delivery failed for %s on job #%s: %s", uid_, result.job_id, err)
        delivered_count = len(outcome.succeeded)
    else:
        delivered_count = 0

    await message.reply_text(
        f"✅ صُنّف كـ «{result.category_name}» — تم إرساله لـ {delivered_count} مستخدم مطابق."
    )


# ================================ LINKEDIN (Layer C) ============================

async def handle_linkedin_url(message, url: str, pipeline_context: str = None):
    if not is_valid_linkedin_job_url(url):
        await message.reply_text("⚠️ الرابط لا يبدو رابط وظيفة LinkedIn صحيح.")
        return
    try:
        result = submit_manual_linkedin_url(db, url, pipeline_context=pipeline_context)
    except ValueError as e:
        await message.reply_text(f"⚠️ {e}")
        return

    base_keyboard = [
        [InlineKeyboardButton("🔗 فتح على LinkedIn", url=url)],
        [InlineKeyboardButton("✅ تم التقديم يدوياً", callback_data=f"linkedin:applied:{result.job_id}")],
    ]

    if result.needs_text:
        await message.reply_text(
            f"💼 تم حفظ رابط LinkedIn (#{result.job_id}) — بدون تصنيف حالياً.\n"
            "إذا لصقت نص الإعلان بنفس الرسالة تحت الرابط، أقدر أصنفه وأوصله لمن يهمه.",
            reply_markup=InlineKeyboardMarkup(base_keyboard),
        )
        return

    if not result.category_matched:
        await message.reply_text(
            f"💼 وظيفة LinkedIn محفوظة (#{result.job_id}) — عنوان غير معروف، أضفته لقائمة المراجعة.",
            reply_markup=InlineKeyboardMarkup(base_keyboard),
        )
        return

    delivered_count = len(result.delivered_to)
    await message.reply_text(
        f"💼 وظيفة LinkedIn محفوظة (#{result.job_id}) — صُنّفت كـ «{result.category_name}» "
        f"وأُرسلت لـ {delivered_count} مستخدم مطابق.",
        reply_markup=InlineKeyboardMarkup(base_keyboard),
    )


# ============================== APPLICATION FLOW ================================

async def do_send(query, job_id: int, uid: str):
    """
    One-tap apply (owner's explicit decision — see src/applications.py
    module docstring): composes the message and sends it immediately from
    the user's own email. No separate preview/confirm step for email.
    """
    from src.applications import ApplicationStateError, send_application_email

    row = db.get_job(job_id)
    emails = json.loads(row["emails"] or "[]")
    if not emails:
        await query.message.reply_text("⚠️ لا يوجد بريد إلكتروني بهذا الإعلان.")
        return

    profile = profiles.get_or_create(uid)
    if not profile.cv_file_path:
        await query.message.reply_text("⚠️ ارفع CV أولاً من 👤 حسابي.")
        return
    if not db.has_smtp_credentials(uid):
        await query.message.reply_text(
            "⚠️ لازم تربط إيميلك الشخصي أولاً حتى ترسل من حسابك بنفسك (ماكو إيميل مشترك).\n"
            "روح لـ 👤 حسابي ← 📧 ربط إيميلي للتقديم."
        )
        return

    app_id = db.create_or_get_application(job_id, uid)
    app_row = db.get_application(app_id)
    if app_row["status"] == "SENT":
        await query.message.reply_text("ℹ️ سبق وقدمت على هذي الوظيفة.")
        return
    if app_row["status"] not in ("PENDING_APPROVAL",):
        await query.message.reply_text(f"ℹ️ حالة طلبك الحالية: {app_row['status']}")
        return

    cats = {c.id: c.canonical_name for c in taxonomy.list_categories()}
    draft = generate_template(
        role_original=cats.get(row["category_id"], ""), role_normalized="", side=None, area=None,
        company_name=row["company_name"], applicant_name="[اسمك]",
    )
    cv_summary = f"مرفق سيرتي الذاتية ({Path(profile.cv_file_path).name})."
    full_body = f"{draft.body}\n\n{cv_summary}"

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, send_application_email, db, app_id, uid, emails[0], draft.subject,
            full_body, Path(profile.cv_file_path),
        )
        await query.message.reply_text(
            f"✅ تم إرسال طلبك من {profile.email}\n📧 إلى: {emails[0]}\n📎 مع CV: {Path(profile.cv_file_path).name}"
        )
    except ApplicationStateError as e:
        await query.message.reply_text(f"⚠️ فشل الإرسال: {e}")


# ================================ CALLBACKS =====================================

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(update.effective_user.id)
    data = query.data or ""

    if data == "noop":
        return

    # --- Registration: category picker ---
    if data.startswith("pick:cat:"):
        cat_id = int(data.split(":")[-1])
        selected = context.user_data.setdefault("reg_selected_categories", set())
        selected.symmetric_difference_update({cat_id})
        await query.edit_message_reply_markup(reply_markup=category_picker_keyboard(selected, "reg:cat:done"))
        return

    if data == "reg:cat:done":
        selected = context.user_data.get("reg_selected_categories", set())
        profiles.set_job_preferences(uid, list(selected))
        await query.message.reply_text("✅ تم حفظ الفئات. الآن اختار مناطقك:")
        await send_area_picker(query.message, uid, context, mode="reg")
        return

    # --- Registration: area picker ---
    if data.startswith("area_nav:open:"):
        target = data.split(":")[-1]
        context.user_data["area_cursor"] = None if target == "root" else target
        selected = context.user_data.get("reg_selected_areas", set())
        await query.edit_message_reply_markup(
            reply_markup=area_picker_keyboard(context.user_data["area_cursor"], selected, mode="reg")
        )
        return

    if data.startswith("pick:area:"):
        node_id = data.split(":")[-1]
        selected = context.user_data.setdefault("reg_selected_areas", set())
        selected.symmetric_difference_update({node_id})
        await query.edit_message_reply_markup(
            reply_markup=area_picker_keyboard(context.user_data.get("area_cursor"), selected, mode="reg")
        )
        return

    if data == "reg:area:done":
        selected = context.user_data.get("reg_selected_areas", set())
        profiles.set_area_preferences(uid, list(selected))
        context.user_data["awaiting_email"] = True
        await query.message.reply_text("✅ تم حفظ المناطق.\n📧 أرسل بريدك الإلكتروني الآن (اختياري — اكتب 'تخطي' لتجاوزه):")
        return

    # --- Main menu ---
    if data == "user:account":
        await send_account_menu(query.message, uid)
        return

    if data == "account:edit_categories":
        await send_category_picker(query.message, uid, context)
        return

    if data == "account:edit_areas":
        await send_area_picker(query.message, uid, context, mode="reg")
        return

    if data == "account:edit_email":
        context.user_data["awaiting_email"] = True
        await query.message.reply_text("📧 أرسل بريدك الإلكتروني الجديد:")
        return

    if data == "account:link_smtp":
        await start_smtp_linking(query.message, context)
        return

    if data.startswith("user:new_jobs"):
        await send_new_jobs(query.message, uid, offset=int(data.split(":")[-1]))
        return

    if data.startswith("user:saved"):
        offset = int(data.split(":")[-1])
        rows = db.list_saved_jobs_for_user(uid, limit=10, offset=offset)
        text = "\n\n".join(render_job_card(r, uid=uid) for r in rows) or "لا يوجد شي محفوظ حالياً."
        await send_long_text(query.message.reply_text, text)
        return

    if data.startswith("user:applications"):
        offset = int(data.split(":")[-1])
        rows = db.list_user_applications(uid, status="SENT", limit=10, offset=offset)
        text = "\n\n".join(render_job_card(r, uid=uid) for r in rows) or "لا توجد تقديمات مرسلة."
        await send_long_text(query.message.reply_text, text)
        return

    if data == "user:my_blacklist":
        companies = db.list_user_ignored_companies_with_ids(uid)
        if not companies:
            await query.message.reply_text("🚫 ماكو شركات محظورة عندك — تقدر تضيف من زر «🚫 تجاهل الشركة» بأي إعلان.")
            return
        for entry in companies:
            await query.message.reply_text(
                f"🚫 {entry['company_name']}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("♻️ إزالة", callback_data=f"my_blacklist:remove:{entry['id']}"),
                ]]),
            )
        return

    if data.startswith("my_blacklist:remove:"):
        entry_id = int(data.split(":")[-1])
        db.remove_user_ignored_company_by_id(uid, entry_id)
        await query.message.reply_text("♻️ تمت الإزالة من قائمتك الشخصية.")
        return

    if data == "user:stats":
        with db._conn() as conn:
            delivered = conn.execute(
                "SELECT COUNT(*) c FROM job_deliveries WHERE telegram_user_id = ?", (uid,)
            ).fetchone()["c"]
            sent = conn.execute(
                "SELECT COUNT(*) c FROM job_applications WHERE telegram_user_id = ? AND status = 'SENT'", (uid,)
            ).fetchone()["c"]
        await query.message.reply_text(f"📊 وصلتك {delivered} وظيفة مطابقة، قدّمت على {sent} منها.")
        return

    if data == "user:help":
        await query.message.reply_text("للمساعدة تواصل مع الأدمن مباشرة، أو اكتب مشكلتك وسأرسلها له.")
        context.user_data["awaiting_help_message"] = True
        return

    # --- Job card actions ---
    if data.startswith("job:full:"):
        job_id = int(data.split(":")[-1])
        row = db.get_job(job_id)
        await send_long_text(query.message.reply_text, f"📄 النص الأصلي:\n\n{row['raw_text']}")
        return

    if data.startswith("job:save:"):
        job_id = int(data.split(":")[-1])
        if db.get_job(job_id) is None:
            await query.message.reply_text("⚠️ هذا الإعلان لم يعد موجوداً.")
            return
        db.save_job_for_user(uid, job_id)
        await query.message.reply_text("⭐ تم الحفظ.")
        return

    if data.startswith("job:ignore:") or data.startswith("job:ignore_company:"):
        job_id = int(data.split(":")[-1])
        row = db.get_job(job_id)
        if row is None:
            await query.message.reply_text("⚠️ هذا الإعلان لم يعد موجوداً.")
            return
        if data.startswith("job:ignore_company:"):
            if row["company_name"]:
                db.add_user_ignored_company(uid, row["company_name"])
                await query.message.reply_text(f"🚫 راح تتجاهل إعلانات {row['company_name']} من الآن (لك انت بس).")
                return
        db.ignore_job_for_user(uid, job_id)
        await query.message.reply_text("❌ تم.")
        return

    if data.startswith("job:apply:"):
        job_id = int(data.split(":")[-1])
        if db.get_job(job_id) is None:
            await query.message.reply_text("⚠️ هذا الإعلان لم يعد موجوداً.")
            return
        await do_send(query, job_id, uid)
        return

    if data.startswith("linkedin:applied:"):
        job_id = int(data.split(":")[-1])
        if db.get_job(job_id) is None:
            await query.message.reply_text("⚠️ هذا الإعلان لم يعد موجوداً.")
            return
        mark_linkedin_applied(db, job_id, uid)
        await query.message.reply_text("✅ تم تسجيل التقديم يدوياً.")
        return

    # --- Admin registration approval ---
    if data.startswith("admin:approve_reg:"):
        target = data.split(":")[-1]
        invites.approve(target)
        await query.message.reply_text(f"✅ تمت الموافقة على {target}.")
        await context.bot.send_message(chat_id=int(target), text="✅ تمت الموافقة على تسجيلك! أرسل /start للمتابعة.")
        return

    if data.startswith("admin:reject_reg:"):
        target = data.split(":")[-1]
        invites.reject(target)
        await query.message.reply_text(f"❌ تم رفض {target}.")
        return

    # --- Admin panel ---
    if data.startswith("admin:"):
        if not is_admin(uid):
            await query.message.reply_text("🚫 هذا القسم للأدمن فقط.")
            return
        await handle_admin_callback(query, context, uid, data)
        return


async def send_account_menu(message, uid: str):
    profile = profiles.get_or_create(uid)
    smtp_linked = db.has_smtp_credentials(uid)
    text = (
        "👤 حسابي\n"
        f"📧 البريد: {profile.email or 'غير مسجل'}\n"
        f"📤 إرسال الطلبات من إيميلي: {'🟢 مربوط' if smtp_linked else '❌ غير مربوط'}\n"
        f"📄 CV: {'مرفوع ✅' if profile.cv_file_path else 'غير مرفوع'}\n"
        f"🎯 عدد الفئات: {len(profile.job_category_ids)}\n"
        f"📍 عدد المناطق: {len(profile.area_node_ids)}"
    )
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تعديل الفئات", callback_data="account:edit_categories"),
         InlineKeyboardButton("📍 تعديل المناطق", callback_data="account:edit_areas")],
        [InlineKeyboardButton("📧 تغيير البريد", callback_data="account:edit_email"),
         InlineKeyboardButton("📤 ربط إيميلي للتقديم", callback_data="account:link_smtp")],
        [InlineKeyboardButton("📄 رفع CV (أرسل الملف مباشرة)", callback_data="noop")],
    ]))


async def send_new_jobs(message, uid: str, offset: int = 0, limit: int = 5):
    with db._conn() as conn:
        rows = conn.execute(
            """SELECT j.* FROM jobs j JOIN job_deliveries d ON j.id = d.job_id
               WHERE d.telegram_user_id = ?
                 AND j.id NOT IN (SELECT job_id FROM user_ignored_jobs WHERE telegram_user_id = ?)
               ORDER BY j.created_at DESC LIMIT ? OFFSET ?""",
            (uid, uid, limit, offset),
        ).fetchall()
    if not rows:
        await message.reply_text("لا توجد وظائف مطابقة حالياً — راح توصلك أول وحدة جديدة تلقائياً.")
        return
    for row in rows:
        await message.reply_text(render_job_card(row, uid=uid), reply_markup=job_contact_keyboard(row, row["id"]))


async def handle_email_input(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str):
    context.user_data["awaiting_email"] = False
    text = update.effective_message.text.strip()
    if text == "تخطي":
        await update.effective_message.reply_text("تم التخطي. يمكنك إضافة بريدك لاحقاً من 👤 حسابي.")
        await send_main_menu(update.effective_message, uid)
        return
    if not is_valid_email(text):
        await update.effective_message.reply_text("⚠️ بريد غير صحيح — حاول مرة أخرى أو اكتب 'تخطي'.")
        context.user_data["awaiting_email"] = True
        return
    profiles.set_email(uid, text)
    await update.effective_message.reply_text(
        "✅ تم حفظ بريدك. لو عندك CV أرسله الآن كملف (اختياري)، أو اضغط /start للمتابعة."
    )
    await send_main_menu(update.effective_message, uid)


async def start_smtp_linking(message, context: ContextTypes.DEFAULT_TYPE):
    """
    Owner requirement: NO shared email account — every user must link their
    OWN email via their own App Password (which requires enabling 2FA on
    their side first). This walks them through it step by step.
    """
    context.user_data["awaiting_smtp_email"] = True
    await message.reply_text(
        "📤 ربط إيميلك للتقديم\n\n"
        "كل طلب توديه يُرسل من إيميلك الشخصي مباشرة — ماكو حساب مشترك إطلاقاً.\n\n"
        "الخطوة 1: اكتب إيميلك (Gmail أو Outlook أو Yahoo تلقائياً، غيرهم تحتاج تدخل الإعدادات يدوياً):"
    )


async def handle_smtp_email_input(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str):
    from src.email_credentials import app_password_setup_link, detect_smtp_provider

    context.user_data["awaiting_smtp_email"] = False
    email = update.effective_message.text.strip()
    if not is_valid_email(email):
        await update.effective_message.reply_text("⚠️ بريد غير صحيح — حاول مرة أخرى.")
        context.user_data["awaiting_smtp_email"] = True
        return

    provider = detect_smtp_provider(email)
    context.user_data["smtp_email"] = email
    if provider:
        host, port = provider
        context.user_data["smtp_host"], context.user_data["smtp_port"] = host, port
        setup_link = app_password_setup_link(host)
        instructions = (
            "الخطوة 2: فعّل «التحقق بخطوتين» (2FA) بحسابك إذا مو مفعّل، "
            "وبعدها ولّد «App Password» (كلمة مرور تطبيقات) — مو الباسورد العادي."
        )
        if setup_link:
            instructions += f"\n🔗 {setup_link}"
        instructions += "\n\nالخطوة 3: الصق الـApp Password هنا (16 رمز تقريباً):"
        await update.effective_message.reply_text(instructions)
        context.user_data["awaiting_smtp_password"] = True
    else:
        await update.effective_message.reply_text(
            "⚠️ ما عرفت مزود بريدك تلقائياً. اكتب SMTP host و port بهذا الشكل:\nsmtp.example.com:587"
        )
        context.user_data["awaiting_smtp_host"] = True


async def handle_smtp_host_input(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str):
    context.user_data["awaiting_smtp_host"] = False
    text = update.effective_message.text.strip()
    if ":" not in text:
        await update.effective_message.reply_text("⚠️ الصيغة: smtp.example.com:587")
        context.user_data["awaiting_smtp_host"] = True
        return
    host, port_str = text.rsplit(":", 1)
    try:
        port = int(port_str)
    except ValueError:
        await update.effective_message.reply_text("⚠️ رقم البورت غير صحيح.")
        context.user_data["awaiting_smtp_host"] = True
        return
    context.user_data["smtp_host"], context.user_data["smtp_port"] = host, port
    await update.effective_message.reply_text(
        "الآن فعّل التحقق بخطوتين على حسابك، ولّد App Password، والصقه هنا:"
    )
    context.user_data["awaiting_smtp_password"] = True


async def handle_smtp_password_input(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str):
    from src.email_credentials import encrypt_password, looks_like_app_password

    context.user_data["awaiting_smtp_password"] = False
    password = update.effective_message.text.strip()

    try:
        await update.effective_message.delete()  # remove the password from chat history for the user's safety
    except Exception:  # noqa: BLE001
        pass

    if not looks_like_app_password(password):
        await update.effective_message.reply_text(
            "⚠️ هذا ما يبدو App Password صحيح (المفروض ~16 رمز بدون رموز خاصة). حاول مرة ثانية."
        )
        context.user_data["awaiting_smtp_password"] = True
        return

    smtp_email = context.user_data.get("smtp_email")
    smtp_host = context.user_data.get("smtp_host")
    smtp_port = context.user_data.get("smtp_port")
    profiles.set_email(uid, smtp_email)
    db.set_smtp_credentials(uid, smtp_host, smtp_port, encrypt_password(password))
    await update.effective_message.reply_text(
        f"✅ تم ربط {smtp_email} — كل طلباتك من الآن ترسل من إيميلك مباشرة."
    )


MAX_CV_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


@registered_only
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from src.cv_manager import is_allowed_cv_file

    uid = str(update.effective_user.id)
    doc = update.message.document

    # H05: never trust the filename as a path — strip any directory
    # components (path traversal, e.g. "../../other_user/cv.pdf") and keep
    # only the basename.
    safe_name = Path(doc.file_name or "cv").name
    if not safe_name or safe_name in (".", ".."):
        safe_name = "cv"

    if not is_allowed_cv_file(safe_name):
        await update.message.reply_text("⚠️ CV يقبل PDF أو Word (.pdf, .doc, .docx) فقط.")
        return

    if doc.file_size and doc.file_size > MAX_CV_FILE_SIZE_BYTES:
        await update.message.reply_text(
            f"⚠️ الملف كبير جداً ({doc.file_size // (1024*1024)}MB) — الحد الأقصى "
            f"{MAX_CV_FILE_SIZE_BYTES // (1024*1024)}MB."
        )
        return

    # H05: each user's CVs live strictly under their own uid-named
    # subdirectory — combined with the basename-only filename above, this
    # keeps one user's upload from ever writing outside their own folder.
    dest_dir = Path("cv_files") / uid
    dest_dir.mkdir(parents=True, exist_ok=True)
    local_path = (dest_dir / safe_name).resolve()
    if dest_dir.resolve() not in local_path.parents and local_path != dest_dir.resolve():
        await update.message.reply_text("⚠️ اسم ملف غير صالح.")
        return

    file = await context.bot.get_file(doc.file_id)
    await file.download_to_drive(custom_path=str(local_path))
    profiles.set_cv(uid, str(local_path))
    await update.message.reply_text(f"✅ تم حفظ CV: {safe_name}")


# ============================== ADMIN PANEL ======================================

def admin_menu_keyboard() -> InlineKeyboardMarkup:
    pending_count = len(invites.list_pending())
    review_label = f"📋 طلبات التسجيل ({pending_count})" if pending_count else "📋 طلبات التسجيل"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(review_label, callback_data="admin:pending_registrations")],
        [InlineKeyboardButton("📋 المراجعة والتصنيف", callback_data="admin:review")],
        [InlineKeyboardButton("🗂 إدارة الفئات", callback_data="admin:categories"),
         InlineKeyboardButton("📍 إدارة المناطق", callback_data="admin:locations")],
        [InlineKeyboardButton("👥 المستخدمون", callback_data="admin:users"),
         InlineKeyboardButton("📢 بث رسالة", callback_data="admin:broadcast")],
        [InlineKeyboardButton("📡 المصادر", callback_data="admin:sources"),
         InlineKeyboardButton("🚫 القوائم المحظورة", callback_data="admin:blacklist")],
        [InlineKeyboardButton("✉️ دعوة مستخدم", callback_data="admin:invite"),
         InlineKeyboardButton("⚙️ الإعدادات", callback_data="admin:settings")],
        [InlineKeyboardButton("📊 التحليلات", callback_data="admin:analytics"),
         InlineKeyboardButton("💾 نسخ احتياطي", callback_data="admin:backup")],
        [InlineKeyboardButton("🔄 إعادة فحص الأرشيف", callback_data="admin:reprocess")],
    ])


async def handle_admin_callback(query, context, uid: str, data: str):
    if data == "admin:menu":
        await query.message.reply_text("🛠 لوحة التحكم", reply_markup=admin_menu_keyboard())
        return

    if data == "admin:pending_registrations":
        pending = invites.list_pending()
        if not pending:
            await query.message.reply_text("لا توجد طلبات تسجيل معلّقة.")
            return
        for p in pending:
            await query.message.reply_text(
                f"👤 طلب من {p['telegram_user_id']} — {p['requested_at']}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ قبول", callback_data=f"admin:approve_reg:{p['telegram_user_id']}"),
                    InlineKeyboardButton("❌ رفض", callback_data=f"admin:reject_reg:{p['telegram_user_id']}"),
                ]]),
            )
        return

    if data == "admin:review":
        pending = taxonomy.list_pending_titles()
        if not pending:
            await query.message.reply_text("لا توجد عناوين قيد المراجعة.")
            return
        for p in pending[:5]:
            categories = taxonomy.list_categories()
            rows = [[InlineKeyboardButton("🆕 فئة جديدة", callback_data=f"admin:pt_new:{p['id']}")]]
            for cat in categories[:8]:
                rows.append([InlineKeyboardButton(f"🔗 مرادف لـ «{cat.canonical_name}»",
                                                    callback_data=f"admin:pt_alias:{p['id']}:{cat.id}")])
            rows.append([InlineKeyboardButton("❌ تجاهل", callback_data=f"admin:pt_reject:{p['id']}")])
            await query.message.reply_text(f"📋 عنوان جديد: «{p['raw_title']}»", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("admin:pt_reject:"):
        taxonomy.reject_pending_title(int(data.split(":")[-1]))
        await query.message.reply_text("❌ تم التجاهل.")
        return

    if data.startswith("admin:pt_alias:"):
        _, _, pending_id, cat_id = data.split(":")
        taxonomy.resolve_as_alias(int(pending_id), int(cat_id))
        await query.message.reply_text("🔗 تم الدمج كمرادف.")
        return

    if data.startswith("admin:pt_new:"):
        pending_id = int(data.split(":")[-1])
        context.user_data["awaiting_new_category_for_pending"] = pending_id
        await query.message.reply_text("اكتب اسم الفئة الجديدة:")
        return

    if data == "admin:categories":
        categories = taxonomy.list_categories()
        text = "\n".join(f"• {c.canonical_name} ({len(c.aliases)} مرادف)" for c in categories) or "لا توجد فئات."
        await query.message.reply_text(
            "🗂 الفئات:\n" + text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ فئة جديدة", callback_data="admin:new_category")]]),
        )
        return

    if data == "admin:new_category":
        context.user_data["awaiting_new_category_name"] = True
        await query.message.reply_text("اكتب اسم الفئة الجديدة:")
        return

    if data == "admin:locations":
        await send_admin_location_browser(query.message, context, cursor_id=None)
        return

    if data.startswith("admin_area_nav:open:"):
        target = data.split(":")[-1]
        await send_admin_location_browser(query.message, context, cursor_id=None if target == "root" else target)
        return

    if data.startswith("admin_area:verify:"):
        location_admin.set_status(data.split(":")[-1], "verified", path=DEFAULT_TREE_PATH)
        tree.reload()
        await query.message.reply_text("✅ تم التوثيق.")
        return

    if data.startswith("admin_area:add_child:"):
        parent_id = data.split(":")[-1]
        context.user_data["awaiting_new_area_parent"] = parent_id
        await query.message.reply_text("اكتب: الاسم_الانكليزي|الاسم_العربي|مرادف1,مرادف2")
        return

    if data == "admin:users":
        all_profiles = profiles.list_all()
        text = f"👥 عدد المستخدمين: {len(all_profiles)}\n\n"
        for p in all_profiles[:15]:
            text += f"• {p.telegram_user_id} — {p.email or 'بلا بريد'} — {'أدمن' if p.is_admin else 'مستخدم'} — CV:{'✅' if p.cv_file_path else '❌'}\n"
        await send_long_text(query.message.reply_text, text)
        return

    if data == "admin:broadcast":
        context.user_data["awaiting_broadcast"] = True
        await query.message.reply_text("اكتب الرسالة اللي تريد بثّها لكل المستخدمين:")
        return

    if data == "admin:sources":
        sources = db.list_sources(platform="telegram")
        if not sources:
            await query.message.reply_text("لا يوجد مصادر. استخدم /addsource @channel")
            return
        for s in sources:
            await query.message.reply_text(
                f"{'🟢' if s['enabled'] else '⚪'} {s['identifier']}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 اختبار", callback_data=f"admin:src_test:{s['id']}"),
                    InlineKeyboardButton("🗑 إزالة", callback_data=f"admin:src_del:{s['id']}"),
                ]]),
            )
        return

    if data.startswith("admin:src_test:"):
        source_id = int(data.split(":")[-1])
        source_row = db.get_source_by_id(source_id)
        if source_row is None:
            await query.message.reply_text("⚠️ المصدر غير موجود (ربما أُزيل).")
            return
        result = await check_source(source_row["identifier"], settings=settings)
        db.set_source_test_result(source_id, result.message)
        icon = "✅" if result.live_ok else ("⚪" if result.live_ok is None else "❌")
        await query.message.reply_text(f"{icon} {result.message}")
        return

    if data.startswith("admin:src_del:"):
        source_id = int(data.split(":")[-1])
        source_row = db.get_source_by_id(source_id)
        if source_row is None:
            await query.message.reply_text("⚠️ المصدر غير موجود (ربما أُزيل).")
            return
        db.remove_source("telegram", source_row["identifier"])
        await query.message.reply_text("🗑 تمت الإزالة.")
        return

    if data == "admin:blacklist":
        companies = db.list_ignored_companies()
        text = "\n".join(companies) if companies else "لا يوجد."
        await query.message.reply_text("🚫 " + text + "\n\nاستخدم /ignorecompany <الاسم>")
        return

    if data == "admin:invite":
        bot_username = BOT_USERNAME_CACHE.get("value") or "your_bot"
        invite = invites.create_invite(bot_username, created_by=uid)
        mode_status = "🟢 مفعّل" if invites.is_invite_only_mode() else "⚪ غير مفعّل"
        await query.message.reply_text(
            f"✉️ رابط الدعوة:\n{invite.link}\n\nوضع الدعوة فقط: {mode_status}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 تبديل وضع الدعوة فقط", callback_data="admin:toggle_invite_mode"),
            ]]),
        )
        return

    if data == "admin:toggle_invite_mode":
        invites.set_invite_only_mode(not invites.is_invite_only_mode())
        await query.message.reply_text(
            f"وضع الدعوة فقط الآن: {'🟢 مفعّل' if invites.is_invite_only_mode() else '⚪ غير مفعّل'}"
        )
        return

    if data == "admin:settings":
        await query.message.reply_text(
            "⚙️ الإعدادات\n"
            f"الإيميل: {'🟢' if settings.email_enabled else '❌'}\n"
            f"الـScanner: {'🟢' if settings.scanner_enabled else '❌'}\n"
            f"التصنيف الذكي: {'🟢' if settings.anthropic_api_key else '⚪'}"
        )
        return

    if data == "admin:analytics":
        stats = db.diagnostics()
        pending = len(taxonomy.list_pending_titles())
        all_profiles = profiles.list_all()
        await query.message.reply_text(
            "📊 التحليلات\n"
            f"مستخدمون: {len(all_profiles)}\n"
            f"إعلانات: {stats['scanned']}\n"
            f"عناوين قيد المراجعة: {pending}\n"
            f"فئات: {len(taxonomy.list_categories())}"
        )
        return

    if data == "admin:backup":
        await query.message.reply_text("⏳ يولّد النسخة الاحتياطية...")
        paths = generate_full_backup(settings.database_path)
        await query.message.reply_document(document=open(paths["db"], "rb"), filename=paths["db"].name)
        await query.message.reply_document(document=open(paths["xlsx"], "rb"), filename=paths["xlsx"].name)
        await query.message.reply_text(
            "✅ للاستعادة لاحقاً: أرسل ملف .db مع الأمر /restore كتعليق.\n"
            "⚠️ ملف الإكسل للعرض فقط — الاستعادة تكون من .db حصراً."
        )
        return

    if data == "admin:reprocess":
        await query.message.reply_text("⏳ يعيد فحص الأرشيف...")

        result = reprocess_archive(db)  # no notify_fn — we send and confirm ourselves below

        pending = db.list_pending_deliveries()  # includes this run's new matches + any past unsent retries
        failed = 0
        for delivery in pending:
            target_uid = delivery["telegram_user_id"]
            row = db.get_job(delivery["job_id"])
            try:
                await context.bot.send_message(
                    chat_id=int(target_uid),
                    text="🔔 وظيفة مطابقة (من إعادة الفحص)\n\n" + render_job_card(row, uid=target_uid),
                    reply_markup=job_contact_keyboard(row, row["id"]),
                )
                db.mark_delivery_sent(row["id"], target_uid)
            except Exception as e:  # noqa: BLE001
                failed += 1
                logger.warning("Reprocess delivery failed for %s: %s", target_uid, e)
            await asyncio.sleep(0.05)  # ~20/sec — stays safely under Telegram's rate limits
        if failed:
            logger.warning("Reprocess: %d/%d deliveries failed.", failed, len(pending))

        await query.message.reply_text(
            f"✅ فُحص {result.jobs_reprocessed} إعلان، {result.new_deliveries} إشعار جديد أُرسل."
        )
        return


async def send_admin_location_browser(message, context, cursor_id):
    children = tree.children(cursor_id)
    for node in children:
        rows = [[
            InlineKeyboardButton("✅ توثيق" if node.status != "verified" else "✔️ موثقة",
                                  callback_data=f"admin_area:verify:{node.id}"),
            InlineKeyboardButton("➕ فرعية", callback_data=f"admin_area:add_child:{node.id}"),
        ]]
        if tree.children(node.id):
            rows.append([InlineKeyboardButton("🔽 فتح", callback_data=f"admin_area_nav:open:{node.id}")])
        await message.reply_text(f"{node.name_ar} ({node.status})", reply_markup=InlineKeyboardMarkup(rows))

    nav = []
    if cursor_id is not None:
        parent = tree.get(cursor_id)
        up = parent.parent_id if parent else None
        nav.append(InlineKeyboardButton("⬆️ للأعلى", callback_data=f"admin_area_nav:open:{up or 'root'}"))
    nav.append(InlineKeyboardButton("➕ عقدة جذر جديدة", callback_data=f"admin_area:add_child:root"))
    await message.reply_text("📍 التنقل:", reply_markup=InlineKeyboardMarkup([nav]))


# ============================ FREE-TEXT ADMIN INPUT HANDLING ====================

async def maybe_handle_admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if the message was consumed as part of an admin text-input flow."""
    text = update.effective_message.text or ""

    if context.user_data.get("awaiting_new_category_name"):
        context.user_data["awaiting_new_category_name"] = False
        taxonomy.create_category(text.strip())
        await update.effective_message.reply_text(f"✅ تمت إضافة الفئة: {text.strip()}")
        return True

    pending_id = context.user_data.get("awaiting_new_category_for_pending")
    if pending_id:
        context.user_data["awaiting_new_category_for_pending"] = None
        taxonomy.resolve_as_new_category(pending_id, text.strip())
        await update.effective_message.reply_text(f"✅ فئة جديدة: {text.strip()}")
        return True

    parent_id = context.user_data.get("awaiting_new_area_parent")
    if parent_id:
        context.user_data["awaiting_new_area_parent"] = None
        try:
            name_en, name_ar, aliases_raw = [p.strip() for p in text.split("|")]
            aliases = [a.strip() for a in aliases_raw.split(",") if a.strip()]
            node_id = name_en.lower().replace(" ", "_")
            location_admin.add_node(node_id, None if parent_id == "root" else parent_id,
                                     name_en, name_ar, aliases, path=DEFAULT_TREE_PATH)
            tree.reload()
            await update.effective_message.reply_text(f"✅ أُضيفت {name_ar} (غير موثقة حتى تراجعها).")
        except (ValueError, IndexError):
            await update.effective_message.reply_text("⚠️ صيغة غير صحيحة: name_en|name_ar|alias1,alias2")
        return True

    if context.user_data.get("awaiting_broadcast"):
        context.user_data["awaiting_broadcast"] = False
        count = 0
        for p in profiles.list_all():
            try:
                await context.bot.send_message(chat_id=int(p.telegram_user_id), text=f"📢 {text}")
                count += 1
            except Exception:  # noqa: BLE001
                pass
        await update.effective_message.reply_text(f"✅ أُرسلت لـ {count} مستخدم.")
        return True

    return False


# ============================ TEXT DISPATCH (registration vs admin vs intake) ====

async def dispatch_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if is_admin(uid) and await maybe_handle_admin_text_input(update, context):
        return
    await on_text_message(update, context)


# ============================ TEXT COMMANDS ======================================

@admin_only
async def cmd_addsource(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استخدام: /addsource @channel_name")
        return
    db.add_source("telegram", context.args[0])
    await update.message.reply_text(f"✅ {context.args[0]}")


@admin_only
async def cmd_ignorecompany(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استخدام: /ignorecompany <الاسم>")
        return
    db.add_ignored_company(" ".join(context.args))
    await update.message.reply_text("🚫 أُضيفت.")


@admin_only
async def cmd_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or not doc.file_name.endswith(".db"):
        await update.message.reply_text("أرسل ملف .db مع الأمر /restore كتعليق.")
        return
    file = await context.bot.get_file(doc.file_id)
    tmp_path = Path("backups") / f"restore_upload_{doc.file_name}"
    tmp_path.parent.mkdir(exist_ok=True)
    await file.download_to_drive(custom_path=str(tmp_path))
    previous = restore_from_db(str(tmp_path), settings.database_path, keep_previous=True)
    await update.message.reply_text(
        f"✅ تمت الاستعادة. نسخة احتياطية من الحالة السابقة: {previous}"
    )


@admin_only
async def cmd_setcv_admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("لرفع CV، أرسل الملف مباشرة كأي مستخدم آخر.")


# =============================== SETUP ===========================================

async def _post_init(application: Application):
    me = await application.bot.get_me()
    BOT_USERNAME_CACHE["value"] = me.username
    await application.bot.set_my_commands([BotCommand("start", "بدء / القائمة الرئيسية")])

    if settings.owner_id:
        try:
            scanner_note = "🟢 المراقبة التلقائية مفعّلة" if settings.scanner_enabled else "⚪ المراقبة التلقائية غير مفعّلة"
            await application.bot.send_message(
                chat_id=int(settings.owner_id),
                text=f"🤖 البوت اشتغل.\n{scanner_note}",
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Could not send startup notification to owner: %s", e)

    if settings.scanner_enabled:
        asyncio.create_task(_run_listener_in_background(application))


async def _run_listener_in_background(application: Application):
    if not settings.scanner_enabled:
        return
    try:
        from src.scanner.telegram_listener import TelegramListener
    except Exception as e:  # noqa: BLE001
        logger.error("Could not import Telegram listener: %s", e)
        return

    channels = [row["identifier"] for row in db.list_sources(platform="telegram") if row["enabled"]]
    if not channels:
        logger.info("Scanner configured but no sources added yet — use /addsource @channel.")
        return

    async def multiuser_scanner_process(raw_text: str, source: dict):
        """
        This is the actual wiring: every new message from a monitored
        channel is split into individual ads (real channels routinely batch
        several ads per message — see src/ad_splitter.py) and each ad goes
        through the exact same multi-user pipeline as Manual Forward Intake
        (src/pipeline_multiuser.py:ingest_ad), including matching, per-user
        personal blacklist filtering, and delivery.
        """
        for ad_text in split_ads(raw_text):
            try:
                result = ingest_ad(db, ad_text, source)  # no notify_fn — send sequentially, rate-limited, below
            except Exception as e:  # noqa: BLE001
                logger.error("Scanner ingestion failed for one ad from %s: %s", source.get("channel"), e)
                continue

            if not result.delivered_to:
                continue
            row = db.get_job(result.job_id)
            for target_uid in result.delivered_to:
                try:
                    await application.bot.send_message(
                        chat_id=int(target_uid),
                        text="🔔 وظيفة جديدة تطابقك\n\n" + render_job_card(row, uid=target_uid),
                        reply_markup=job_contact_keyboard(row, result.job_id),
                    )
                    db.mark_delivery_sent(result.job_id, target_uid)
                except Exception as e:  # noqa: BLE001
                    logger.error("Failed to notify %s about job #%s: %s", target_uid, result.job_id, e)
                await asyncio.sleep(0.05)  # ~20/sec — stays safely under Telegram's rate limits

    try:
        listener = TelegramListener(settings, db, process_message_fn=multiuser_scanner_process)
    except (RuntimeError, ValueError) as e:
        logger.error("Could not start Telegram listener: %s", e)
        await _notify_owner_of_scanner_issue(application, f"⚠️ تعذر تشغيل مراقبة القنوات: {e}")
        return

    logger.info("Starting multi-user Telegram scanner for %d channel(s).", len(channels))
    try:
        await listener.run(channels)
    except Exception as e:  # noqa: BLE001
        logger.error("Telegram listener stopped unexpectedly: %s", e)
        await _notify_owner_of_scanner_issue(application, f"⚠️ توقفت مراقبة القنوات بشكل غير متوقع: {e}")


async def _notify_owner_of_scanner_issue(application: Application, text: str):
    if not settings.owner_id:
        return
    try:
        await application.bot.send_message(chat_id=int(settings.owner_id), text=text)
    except Exception as e:  # noqa: BLE001
        logger.error("Could not notify owner of scanner issue: %s", e)


def build_application() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
    app = Application.builder().token(settings.telegram_bot_token).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("addsource", cmd_addsource))
    app.add_handler(CommandHandler("ignorecompany", cmd_ignorecompany))
    app.add_handler(CommandHandler("restore", cmd_restore))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.Document.ALL & filters.CaptionRegex(r"(?i)/restore"), cmd_restore))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, dispatch_text))
    return app


if __name__ == "__main__":
    application = build_application()
    logger.info("Job Hunter multi-user bot starting (polling mode).")
    # Python 3.14 compat: ensure an event loop exists before PTB run_polling()
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    application.run_polling()
