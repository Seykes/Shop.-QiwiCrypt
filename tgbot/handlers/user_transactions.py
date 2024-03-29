# - *- coding: utf- 8 - *-
from aiogram.dispatcher import FSMContext
from aiogram.types import CallbackQuery, Message
from aiocryptopay import AioCryptoPay

from tgbot.data.loader import dp
from tgbot.data.config import get_admins, get_crypto_token
from tgbot.keyboards.inline_user import refill_bill_finl, refill_choice_finl, refill_choice_crypt, check_crypto_bot_kb
from tgbot.services.api_qiwi import QiwiAPI
from tgbot.services.api_sqlite import update_userx, get_refillx, add_refillx, get_userx
from tgbot.utils.const_functions import get_date, get_unix
from tgbot.utils.misc_functions import send_admins

min_input_qiwi = 5  # Минимальная сумма пополнения в рублях


# Выбор способа пополнения
@dp.callback_query_handler(text="user_refill", state="*")
async def refill_way(call: CallbackQuery, state: FSMContext):
    get_kb = refill_choice_finl()

    if get_kb is not None:
        await call.message.edit_text("<b>💰 Выберите способ пополнения</b>", reply_markup=get_kb)
    else:
        await call.answer("⛔ Пополнение временно недоступно", True)


# Выбор способа пополнения
@dp.callback_query_handler(text_startswith="refill_choice", state="*")
async def refill_way_choice(call: CallbackQuery, state: FSMContext):
    get_way = call.data.split(":")[1]

    await state.update_data(here_pay_way=get_way)

    await state.set_state("here_pay_amount")
    await call.message.edit_text("<b>💰 Введите сумму пополнения</b>")

async def get_crypto_bot_sum(summa: float, currency: str):
    cryptopay = AioCryptoPay(get_crypto_token(), network="https://pay.crypt.bot")
    courses = await cryptopay.get_exchange_rates()
    await cryptopay.close()
    for course in courses:
        if course.source == currency and course.target == 'RUB':
            return summa / course.rate

    
@dp.callback_query_handler(state="here_currency")
async def refill_crypt(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.finish()
    
    amount = data['amount']
    currency = call.data.split("|")[1]
    
    cryptopay = AioCryptoPay(get_crypto_token(), network="https://pay.crypt.bot")
    invoice = await cryptopay.create_invoice(
            asset=currency,
            amount=await get_crypto_bot_sum(
                amount,
                currency
            )
        )
    await cryptopay.close()
    
    await state.set_state("wait_pay")
    await state.set_data({"amount": amount})
    await call.message.answer(
        f'<b>💸 Отправьте {amount}RUB <a href="{invoice.pay_url}">По ссылке</a></b>',
        reply_markup=check_crypto_bot_kb(invoice.pay_url, invoice.invoice_id)
    )


async def check_crypto_bot_invoice(invoice_id: int):
    cryptopay = AioCryptoPay(get_crypto_token(), network="https://pay.crypt.bot")
    invoice = await cryptopay.get_invoices(invoice_ids=invoice_id)
    await cryptopay.close()
    if invoice.status == 'paid':
        return True
    else:
        return False


@dp.callback_query_handler(text_startswith="check_crypto_bot", state="wait_pay")
async def check_crypto_bot(call: CallbackQuery, state: FSMContext):
    
    if await check_crypto_bot_invoice(int(call.data.split('|')[1])):
        data = await state.get_data()
        sum = data['amount']
        await call.answer(
            '✅ Оплата прошла успешно!',
            show_alert=True
        )
        await call.message.delete()
        await call.message.answer(
            f'<b>💸 Ваш баланс пополнен на сумму {sum}RUB!</b>'
        )
        
        await refill_success(call, "None", sum, "CryptoBot")
    else:
        await call.answer(
            '❗️ Вы не оплатили счёт!',
            show_alert=True
        )


###################################################################################
#################################### ВВОД СУММЫ ###################################
# Принятие суммы для пополнения средств через QIWI
@dp.message_handler(state="here_pay_amount")
async def refill_get(message: Message, state: FSMContext):
    if message.text.isdigit():
        get_way = (await state.get_data())['here_pay_way']
        
        if get_way == "CryptoBot":
            await state.set_state("here_currency")
            await state.set_data({"here_pay_way": get_way, "amount": int(message.text)})
            
            return await message.answer("<b>💰 Выберите валюту</b>", reply_markup=refill_choice_crypt())
        
        cache_message = await message.answer("<b>♻ Подождите, платёж генерируется...</b>")
        pay_amount = int(message.text)
        

        if min_input_qiwi <= pay_amount <= 300000:
            await state.finish()

            if get_way != "CryptoBot":
                get_message, get_link, receipt = await (
                    await QiwiAPI(cache_message, user_bill_pass=True)
                ).bill_pay(pay_amount, get_way)

            if get_message:
                await cache_message.edit_text(get_message, reply_markup=refill_bill_finl(get_link, receipt, get_way))
        else:
            await cache_message.edit_text(f"<b>❌ Неверная сумма пополнения</b>\n"
                                          f"▶ Cумма не должна быть меньше <code>{min_input_qiwi}₽</code> и больше <code>300 000₽</code>\n"
                                          f"💰 Введите сумму для пополнения средств")
    else:
        await message.answer("<b>❌ Данные были введены неверно.</b>\n"
                             "💰 Введите сумму для пополнения средств")


###################################################################################
################################ ПРОВЕРКА ПЛАТЕЖЕЙ ################################
# Проверка оплаты через форму
@dp.callback_query_handler(text_startswith="Pay:Form")
async def refill_check_form(call: CallbackQuery):
    receipt = call.data.split(":")[2]

    pay_status, pay_amount = await (
        await QiwiAPI(call, user_check_pass=True)
    ).check_form(receipt)

    if pay_status == "PAID":
        get_refill = get_refillx(refill_receipt=receipt)
        if get_refill is None:
            await refill_success(call, receipt, pay_amount, "Form")
        else:
            await call.answer("❗ Ваше пополнение уже было зачислено.", True)
    elif pay_status == "EXPIRED":
        await call.message.edit_text("<b>❌ Время оплаты вышло. Платёж был удалён.</b>")
    elif pay_status == "WAITING":
        await call.answer("❗ Платёж не был найден.\n"
                          "⌛ Попробуйте чуть позже.", True, cache_time=5)
    elif pay_status == "REJECTED":
        await call.message.edit_text("<b>❌ Счёт был отклонён.</b>")


# Проверка оплаты по переводу (по нику или номеру)
@dp.callback_query_handler(text_startswith=['Pay:Number', 'Pay:Nickname'])
async def refill_check_send(call: CallbackQuery):
    way_pay = call.data.split(":")[1]
    receipt = call.data.split(":")[2]

    pay_status, pay_amount = await (
        await QiwiAPI(call, user_check_pass=True)
    ).check_send(receipt)

    if pay_status == 1:
        await call.answer("❗ Оплата была произведена не в рублях.", True, cache_time=5)
    elif pay_status == 2:
        await call.answer("❗ Платёж не был найден.\n"
                          "⌛ Попробуйте чуть позже.", True, cache_time=5)
    elif pay_status == 4:
        pass
    else:
        get_refill = get_refillx(refill_receipt=receipt)
        if get_refill is None:
            await refill_success(call, receipt, pay_amount, way_pay)
        else:
            await call.answer("❗ Ваше пополнение уже зачислено.", True, cache_time=60)


##########################################################################################
######################################### ПРОЧЕЕ #########################################
# Зачисление средств
async def refill_success(call: CallbackQuery, receipt, amount, get_way):
    get_user = get_userx(user_id=call.from_user.id)

    add_refillx(get_user['user_id'], get_user['user_login'], get_user['user_name'], receipt,
                amount, receipt, get_way, get_date(), get_unix())

    update_userx(call.from_user.id,
                 user_balance=get_user['user_balance'] + amount,
                 user_refill=get_user['user_refill'] + amount)

    await call.message.edit_text(f"<b>💰 Вы пополнили баланс на сумму <code>{amount}₽</code>. Удачи ❤\n"
                                 f"🧾 Чек: <code>#{receipt}</code></b>")

    await send_admins(
        f"👤 Пользователь: <b>@{get_user['user_login']}</b> | <a href='tg://user?id={get_user['user_id']}'>{get_user['user_name']}</a> | <code>{get_user['user_id']}</code>\n"
        f"💰 Сумма пополнения: <code>{amount}₽</code>\n"
        f"🧾 Чек: <code>#{receipt}</code>"
    )
