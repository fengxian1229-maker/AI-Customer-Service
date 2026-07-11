'use strict';

// Source: ../workspace-autoreply-clean/lc-rich.js and case-replies.js.
// Only copied text that exists in the old version. Missing new-flow wording stays missing.

const FLOW_MESSAGES = {
  menu_reprompt: {
    es: [
      'Para ayudarle sin confundir el caso, por favor elija una opción del menú.',
      'Para dirigir su caso correctamente, seleccione una opción del menú.',
      'Así puedo ayudarle mejor y sin demoras: elija la opción que corresponde a su caso.',
    ],
    zh: [
      '為了避免案件判斷錯誤，請先從選單選擇你要處理的問題。',
      '為了正確接住你的問題，請先從選單選擇對應項目。',
      '請先點選最符合你情況的選單，這樣我們才能照正確流程協助。',
    ],
    en: [
      'To avoid routing the case incorrectly, please choose one option from the menu.',
      'Please choose one menu option so I can route your case correctly.',
      'So I can help without delay, please choose the option that matches your case.',
    ],
  },
  menu_button_reminder: {
    es: [
      'El menú principal está al inicio de este chat. Para ayudarle correctamente, por favor toque la opción que corresponda a su consulta.',
      'El menú principal está al inicio de este chat. Por favor seleccione una opción para continuar.',
      'Para no confundir el caso, toque el botón que corresponda a su consulta. El menú principal está al inicio de este chat.',
    ],
    zh: [
      '主選單在本次對話開頭。為了正確協助你，請點選最符合你問題的按鈕。',
      '主選單在本次對話開頭。請先點選對應按鈕，我們才能照正確流程處理。',
      '為了避免判斷錯誤，請點選最符合你問題的選項；主選單在本次對話開頭。',
    ],
    en: [
      'The main menu is at the beginning of this chat. To help you correctly, please tap the option that matches your question.',
      'The main menu is at the beginning of this chat. Please choose one option to continue.',
      'To avoid routing the case incorrectly, tap the button that matches your question. The main menu is at the beginning of this chat.',
    ],
  },
  menu_deposit_button_reminder: {
    es: [
      'El menú principal está al inicio de este chat. Si es un problema de depósito, toque «Problemas de depósito» para continuar.',
      'Si su recarga no llegó, el menú principal está al inicio de este chat. Por favor toque «Problemas de depósito».',
      'Para consultar ese depósito, seleccione «Problemas de depósito» en el menú principal al inicio de este chat.',
    ],
    zh: [
      '主選單在本次對話開頭。如果是存款問題，請先點「存款問題」。',
      '如果是充值後沒到帳，請到本次對話開頭的主選單點「存款問題」。',
      '要查這筆存款，請先點本次對話開頭主選單中的「存款問題」。',
    ],
    en: [
      'The main menu is at the beginning of this chat. If this is a deposit issue, tap “Deposit issues” to continue.',
      'If your deposit was not credited, the main menu is at the beginning of this chat. Please choose “Deposit issues”.',
      'To check that deposit, select “Deposit issues” from the main menu at the beginning of this chat.',
    ],
  },
  menu_withdrawal_button_reminder: {
    es: [
      'El menú principal está al inicio de este chat. Si es un problema de retiro, toque «Problemas de retiro» para continuar.',
      'Si su consulta es sobre retiro, el menú principal está al inicio de este chat. Por favor seleccione «Problemas de retiro».',
      'Para revisar un retiro, seleccione «Problemas de retiro» en el menú principal al inicio de este chat.',
    ],
    zh: [
      '主選單在本次對話開頭。如果是提款問題，請先點「提款問題」。',
      '如果你的問題和提款有關，請到本次對話開頭的主選單點「提款問題」。',
      '要處理提款問題，請先點本次對話開頭主選單中的「提款問題」。',
    ],
    en: [
      'The main menu is at the beginning of this chat. If this is a withdrawal issue, tap “Withdrawal issues” to continue.',
      'If your question is about a withdrawal, the main menu is at the beginning of this chat. Please choose “Withdrawal issues”.',
      'To review a withdrawal, select “Withdrawal issues” from the main menu at the beginning of this chat.',
    ],
  },
  withdrawal_menu_button_reminder: {
    es: [
      'Para continuar con el retiro, toque una opción del menú de retiro de arriba.',
      'Por favor seleccione el tipo de caso de retiro en el menú de arriba.',
      'Para ayudarle con el retiro, elija una de las opciones de retiro mostradas arriba.',
    ],
    zh: [
      '請先點上方提款選單中的一個選項，我們才能繼續處理。',
      '請從上方提款選單選擇你的提款情況。',
      '為了協助提款問題，請選擇上方其中一個提款選項。',
    ],
    en: [
      'To continue with the withdrawal, tap one option from the withdrawal menu above.',
      'Please choose the withdrawal case type from the menu above.',
      'To help with the withdrawal, select one of the withdrawal options shown above.',
    ],
  },
  menu_visibility_resend: {
    es: [
      'Entiendo que no ve las opciones. Se las envío nuevamente abajo; por favor toque la que corresponde a su caso.',
      'Le reenvío el menú para que pueda continuar. Si aún no aparece, le pasaré con atención humana.',
      'Gracias por avisar. Enseguida le muestro las opciones otra vez para que pueda elegir.',
    ],
    zh: [
      '我了解你看不到選項，我會在下方重新送出選單，請點選符合你問題的項目。',
      '我重新發送選單給你。如果還是看不到，我會幫你轉接真人客服。',
      '謝謝你告知，我現在重新顯示選項，方便你繼續選擇。',
    ],
    en: [
      'I understand you cannot see the options. I am sending them again below; please tap the one that matches your case.',
      'I am resending the menu so you can continue. If it still does not appear, I will transfer you to live support.',
      'Thank you for letting me know. I will show the options again so you can choose.',
    ],
  },
  deposito_ask_username: {
    es: [
      'Para revisar su depósito, envíe usuario o teléfono y el comprobante de pago.',
      'Para consultar el depósito, compártanos su usuario o teléfono registrado junto con el comprobante.',
      'Envíe su usuario o teléfono registrado y la captura del pago para poder revisar el depósito.',
    ],
    zh: [
      '為了查詢你的存款，請提供用戶名或註冊手機號，並上傳付款截圖。',
      '請提供用戶名或註冊手機號，並附上付款成功截圖，我們才能確認存款。',
      '要幫你查存款，需要你的用戶名或註冊手機號，加上付款憑證截圖。',
    ],
    en: [
      'To check your deposit, please send your username or registered phone and the payment receipt.',
      'To review the deposit, please share your username or registered phone together with the receipt.',
      'Send your username or registered phone and the payment screenshot so we can check the deposit.',
    ],
  },
  deposito_ask_username_after_image: {
    es: [
      'Recibí el comprobante. Envíe también usuario o teléfono para poder consultar.',
      'Gracias, ya tenemos la captura. Falta su usuario o teléfono registrado para revisar.',
      'La imagen fue recibida. Para continuar, envíe su usuario o teléfono registrado.',
    ],
    zh: [
      '已收到付款截圖。請再提供用戶名或註冊手機號，我才能幫你查詢。',
      '付款截圖已收到，還需要你的用戶名或註冊手機號才能繼續確認。',
      '截圖這邊收到囉，請再補用戶名或註冊手機號，我們就能查詢。',
    ],
    en: [
      'I received the receipt. Please also send your username or registered phone so I can check it.',
      'Thank you, we have the screenshot. Please send your username or registered phone to continue.',
      'The image was received. To continue, please send your username or registered phone.',
    ],
  },
  deposito_ask_slip: {
    es: [
      'Gracias. Envíe el comprobante de pago con «+» o adjuntar; abajo hay ejemplo.',
      'Falta el comprobante de pago. Puede subirlo con «+» o adjuntar.',
      'Para continuar, envíe la captura del pago; abajo verá el ejemplo.',
    ],
    zh: [
      '收到。請傳付款截圖，可點「+」或附件上傳；下方有範例圖。',
      '謝謝，還需要付款截圖；可用「+」或附件上傳，下方有範例。',
      '為了繼續查詢，請上傳付款截圖；下方有範例可參考。',
    ],
    en: [
      'Received. Please send the payment receipt. Tap “+” or attach to upload it; an example is below.',
      'Thank you. We still need the payment receipt; you can upload it with “+” or attach. An example is below.',
      'To continue, send the payment screenshot with “+” or attach. The example is below.',
    ],
  },
  deposito_done: {
    es: [
      'Listo, ya recibimos los datos de su depósito. Lo revisaremos con cuidado y le avisaremos por aquí apenas tengamos novedades. Su dinero está 100% seguro dentro de nuestro proceso.',
      'Gracias, la información de su depósito quedó registrada. Vamos a darle seguimiento y le escribiremos en este chat cuando haya actualización. Su dinero está 100% seguro dentro de nuestro proceso.',
      'Recibimos los datos del caso. Seguiremos revisando su depósito y le mantendremos informado por aquí. Su dinero está 100% seguro dentro de nuestro proceso.',
    ],
    zh: [
      '收到，已收到你的存款案件資料，我們會仔細確認，有更新會在這裡通知你。您的資金在我們的流程底下是百分之百安全的。',
      '謝謝，存款資料已登記進入確認流程，我們會持續追蹤並在這個聊天室回覆你。您的資金在我們的流程底下是百分之百安全的。',
      '案件資料已收到，我們會繼續確認這筆存款並在此更新你。請放心，您的資金在我們的流程底下是百分之百安全的。',
    ],
    en: [
      'Got it, we received the deposit case details. We will check it carefully and update you here when there is news. Your funds are 100% safe within our process.',
      'Thank you. The deposit information is registered for review, and we will follow up in this chat. Your funds are 100% safe within our process.',
      'Received. We will keep checking the deposit and reply in this chat. Your money is 100% safe within our process.',
    ],
  },
  retiro_ask_username: {
    es: [
      'Para revisar su retiro, envíe usuario o teléfono y la captura del retiro.',
      'Para consultar el retiro, compártanos su usuario o teléfono registrado junto con la captura de la solicitud.',
      'Envíe su usuario o teléfono registrado y la captura del retiro para poder revisarlo.',
    ],
    zh: [
      '為了查詢你的提款，請提供用戶名或註冊手機號，並上傳提款截圖。',
      '請提供用戶名或註冊手機號，並附上提款申請截圖，我們才能確認提款。',
      '要幫你查提款，需要你的用戶名或註冊手機號，加上提款截圖。',
    ],
    en: [
      'To check your withdrawal, please send your username or registered phone and the withdrawal screenshot.',
      'To review the withdrawal, please share your username or registered phone together with the request screenshot.',
      'Send your username or registered phone and the withdrawal screenshot so we can check it.',
    ],
  },
  retiro_ask_username_after_image: {
    es: [
      'Recibí la captura. Envíe también usuario o teléfono para poder consultar.',
      'Gracias, ya tenemos la imagen. Falta su usuario o teléfono registrado para revisar el retiro.',
      'La captura fue recibida. Para continuar, envíe su usuario o teléfono registrado.',
    ],
    zh: [
      '已收到提款截圖。請再提供用戶名或註冊手機號，我才能幫你查詢。',
      '提款截圖已收到，還需要你的用戶名或註冊手機號才能繼續確認。',
      '截圖這邊收到囉，請再補用戶名或註冊手機號，我們就能查詢。',
    ],
    en: [
      'I received the screenshot. Please also send your username or registered phone so I can check it.',
      'Thank you, we have the image. Please send your username or registered phone to review the withdrawal.',
      'The screenshot was received. To continue, please send your username or registered phone.',
    ],
  },
  retiro_ask_slip: {
    es: [
      'Gracias. Envíe la captura del retiro con «+» o adjuntar; abajo hay ejemplo.',
      'Falta la captura del retiro. Puede subirla con «+» o adjuntar.',
      'Para continuar, envíe la captura del retiro; abajo verá el ejemplo.',
    ],
    zh: [
      '收到。請傳提款申請截圖，可點「+」或附件上傳；下方有範例圖。',
      '謝謝，還需要提款截圖；可用「+」或附件上傳，下方有範例。',
      '為了繼續查詢，請上傳提款截圖；下方有範例可參考。',
    ],
    en: [
      'Received. Please send the withdrawal request screenshot. Tap “+” or attach; an example is below.',
      'Thank you. We still need the withdrawal screenshot; you can upload it with “+” or attach. An example is below.',
      'To continue, send the withdrawal screenshot with “+” or attach. The example is below.',
    ],
  },
  retiro_done: {
    es: [
      'Listo, ya recibimos los datos de su retiro. Lo revisaremos con cuidado y le avisaremos por aquí apenas tengamos novedades. Su dinero está 100% seguro dentro de nuestro proceso.',
      'Gracias, la información de su retiro quedó registrada. Vamos a darle seguimiento y le escribiremos en este chat cuando haya actualización. Su dinero está 100% seguro dentro de nuestro proceso.',
      'Recibimos los datos del caso. Seguiremos revisando su retiro y le mantendremos informado por aquí. Su dinero está 100% seguro dentro de nuestro proceso.',
    ],
    zh: [
      '收到，已收到你的提款案件資料，我們會仔細確認，有更新會在這裡通知你。您的資金在我們的流程底下是百分之百安全的。',
      '謝謝，提款資料已登記進入確認流程，我們會持續追蹤並在這個聊天室回覆你。您的資金在我們的流程底下是百分之百安全的。',
      '案件資料已收到，我們會繼續確認這筆提款並在此更新你。請放心，您的資金在我們的流程底下是百分之百安全的。',
    ],
    en: [
      'Got it, we received the withdrawal case details. We will check it carefully and update you here when there is news. Your funds are 100% safe within our process.',
      'Thank you. The withdrawal information is registered for review, and we will follow up in this chat. Your funds are 100% safe within our process.',
      'Received. We will keep checking the withdrawal and reply in this chat. Your money is 100% safe within our process.',
    ],
  },
  customer_resolved_ack: {
    es: [
      'Perfecto, me alegra que ya se resolvió. Escríbanos si necesita algo más.',
      'Qué bueno que ya quedó resuelto. Si necesita otra ayuda, puede escribirnos de nuevo.',
      'Excelente, nos alegra que se haya solucionado. Estamos atentos si necesita algo más.',
    ],
    zh: [
      '太好了，問題已解決就好。有其他需要再隨時聯繫我們。',
      '太好了，有順利處理好就好。之後還需要協助可以再找我們。',
      '了解，問題解決就好；如果還有其他狀況，隨時再聯繫我們。',
    ],
    en: [
      'Great, glad it is resolved. Write us if you need anything else.',
      'Good to hear it is solved. If you need more help, you can write us again.',
      'Excellent, glad it was solved. We are here if you need anything else.',
    ],
  },
  backend_ack_waiting: {
    es: [
      'De acuerdo, gracias por confirmarlo. Seguimos atentos a la revisión y le avisaremos por aquí cuando haya una actualización.',
      'Gracias por responder. Su caso sigue en seguimiento; apenas tengamos novedades, se las informaremos en este mismo chat.',
      'Entendido. Vamos a mantener el caso en revisión y le responderemos aquí en cuanto tengamos una actualización.',
    ],
    zh: [
      '好的，謝謝你回覆。我們會繼續留意這筆案件，有更新會在這裡通知你。',
      '收到，這筆案件我們會持續追蹤；只要有新進度，會在這個聊天室回覆你。',
      '了解，我們會讓案件維持在確認中，後續有更新會在這裡告知你。',
    ],
    en: [
      'Understood. We will keep watching the review and update you here when there is news.',
      'Thank you for confirming. The case remains in follow-up; we will notify you in this chat with any update.',
      'Got it. We will keep the case under review and reply here when we have an update.',
    ],
  },
  idle_customer_check: {
    es: [
      'Disculpe, solo quiero asegurarme de que su consulta no quede pendiente. Si todavía necesita ayuda, escríbanos por aquí y seguimos atentos.',
      'Seguimos aquí para ayudarle. Si aún desea continuar o tiene otra duda, puede responder en este chat y con gusto lo revisamos.',
      'Quiero confirmar que todo esté bien por su lado. Si todavía necesita apoyo, díganos aquí y seguimos acompañándole.',
    ],
    zh: [
      '不好意思，想跟你確認一下問題有沒有還需要協助，避免你的事情停在這裡。如果還要繼續處理，可以直接回覆我們。',
      '我們還在這裡協助你。如果你還想繼續處理，或有其他問題，可以直接在這個聊天室告訴我們。',
      '想確認你那邊是否都還順利。如果還需要幫忙，直接在這裡回覆，我們會繼續協助。',
    ],
    en: [
      'Sorry to bother you, I just want to make sure your question does not stay pending. If you still need help, reply here and we will continue.',
      'We are still here to help. If you would like to continue or have another question, you can reply in this chat and we will check it.',
      'I want to confirm everything is okay on your side. If you still need support, let us know here and we will keep helping.',
    ],
  },
  idle_customer_closing: {
    es: 'Si no tiene otra consulta, cerraré este chat.',
    zh: '如果沒有其他問題，我將關閉這個聊天。',
    en: 'If there are no other questions, I will close this chat.',
  },
  case_forward_failed_handoff: {
    es: [
      'Tuvimos un problema al enviar el caso al equipo interno. Para que no se detenga, lo paso a un agente ahora.',
      'No se pudo enviar el caso correctamente al equipo interno. Para evitar demora, le paso con un agente.',
      'El envío interno no se completó bien. Le transferiré a atención humana para que el caso continúe.',
    ],
    zh: [
      '案件送往內部團隊時遇到問題。為避免停住，我現在為你轉接真人客服。',
      '案件沒有成功送到內部團隊。為了避免延誤，我會幫你轉給真人客服。',
      '內部送件沒有完成，我會轉接專員，讓這個案件繼續處理。',
    ],
    en: [
      'There was a problem sending the case to the internal team. To avoid delay, I am transferring you to a live agent now.',
      'The case could not be sent correctly to the internal team. To avoid delay, I will transfer you to an agent.',
      'The internal handoff did not complete. I will transfer you to live support so the case can continue.',
    ],
  },
  deposit_howto_tutorial: {
    es: 'Para realizar una recarga:\n\n1. Ingrese a su cuenta.\n2. Vaya a la sección Depósito / Recarga.\n3. Elija el método de pago disponible.\n4. Ingrese el monto que desea recargar.\n5. Complete el pago en la página o aplicación indicada.\n6. Guarde la captura del comprobante de pago.\n\nPor favor realice la operación una vez. Si ya completó el pago pero el saldo no llegó a su cuenta de juego, vuelva al menú y seleccione «Depósito no acreditado».',
    zh: '若你要進行充值，請依照以下步驟操作：\n\n1. 登入你的遊戲帳號。\n2. 進入「充值 / 存款」頁面。\n3. 選擇目前可用的付款方式。\n4. 輸入你要充值的金額。\n5. 按照頁面指示完成付款。\n6. 付款完成後，請保留付款成功截圖。\n\n請先實際操作一次。若你已經完成付款，但遊戲帳號仍未到帳，請回到選單並選擇「存款未到帳」。',
    en: 'To make a deposit:\n\n1. Log in to your account.\n2. Go to the Deposit / Recharge section.\n3. Choose an available payment method.\n4. Enter the amount you want to deposit.\n5. Complete the payment on the indicated page or app.\n6. Keep a screenshot of the successful payment receipt.\n\nPlease try it once. If you already completed the payment but the balance did not reach your game account, return to the menu and choose “Deposit not credited”.',
  },
  withdrawal_howto_tutorial: {
    es: 'Para solicitar un retiro:\n\n1. Ingrese a su cuenta.\n2. Vaya a la sección Retiro.\n3. Verifique que sus datos de retiro estén correctos.\n4. Si debe vincular billetera/cuenta, vaya a Mi cuenta / Mis tarjetas, agregue la billetera y vuelva a Retiro.\n5. Si no ve el campo para ingresar el monto, deslice hacia abajo dentro de la página de retiro hasta encontrar la sección de monto.\n6. Ingrese el monto que desea retirar.\n7. Confirme y envíe la solicitud de retiro.\n8. Guarde una captura de la solicitud de retiro.\n\nPor favor realice la operación una vez. Si ya solicitó el retiro pero todavía no recibió el dinero, vuelva al menú y seleccione «Retiro no recibido». Si no aparece el campo de monto o la página no le permite continuar, seleccione «No puedo retirar».',
    zh: '若你要申請提款，請依照以下步驟操作：\n\n1. 登入你的遊戲帳號。\n2. 進入「提款 / 取款」頁面。\n3. 確認你的提款資料是否正確。\n4. 如果沒有看到輸入金額的欄位，請在提款頁面內往下滑，找到金額輸入區。\n5. 輸入你要提款的金額。\n6. 確認並提交提款申請。\n7. 提交後，請保留提款申請截圖。\n\n請先實際操作一次。若你已經提交提款申請，但仍未收到款項，請回到選單並選擇「提款未到帳」。如果沒有出現金額欄位或頁面不讓你繼續，請選擇「無法提款」。',
    en: 'To request a withdrawal:\n\n1. Log in to your account.\n2. Go to the Withdrawal section.\n3. Check that your withdrawal details are correct.\n4. If you do not see the amount field, scroll down inside the withdrawal page until you find the amount section.\n5. Enter the amount you want to withdraw.\n6. Confirm and submit the withdrawal request.\n7. Keep a screenshot of the withdrawal request.\n\nPlease try it once. If you already submitted the withdrawal but still have not received the money, return to the menu and choose “Withdrawal not received”. If the amount field does not appear or the page does not let you continue, choose “Cannot withdraw”.',
  },
  withdrawal_blocked_tutorial: {
    es: 'Si no puede retirar, muchas veces se debe al requisito de apuesta/rollover.\n\n1. Ingrese a su cuenta.\n2. Vaya a la página de Billetera / Wallet.\n3. Revise en la parte inferior si aparece un monto pendiente de apuesta/rollover.\n\nPara ayudarle a calcular cuánto falta, envíe su nombre de usuario o teléfono registrado.',
    zh: '如果你無法提款，常見原因是還有流水 / 投注量未完成。\n\n1. 登入你的帳號。\n2. 進入「錢包 / Wallet」頁面。\n3. 在頁面下方查看是否有待完成的流水額度。\n\n為了幫你計算還差多少流水，請提供你的用戶名或註冊手機號。',
    en: 'If you cannot withdraw, it is often because a wagering/turnover requirement is still pending.\n\n1. Log in to your account.\n2. Go to Wallet.\n3. Check the bottom of the page for any pending wagering/turnover amount.\n\nTo help calculate how much is still missing, please send your username or registered phone number.',
  },
  withdrawal_blocked_ask_username: {
    es: [
      'Por favor envíe usuario o teléfono registrado para calcular el rollover.',
      'Para calcular el rollover pendiente, envíe su usuario o teléfono registrado.',
      'Compártanos su usuario o teléfono registrado y revisaremos cuánto rollover falta.',
    ],
    zh: [
      '請提供用戶名或註冊手機號，我們幫你計算還差多少流水。',
      '為了幫你查流水，請提供用戶名或註冊手機號。',
      '請傳你的用戶名或註冊手機號，我們會確認目前還差多少流水。',
    ],
    en: [
      'Please send your username or registered phone so we can calculate the pending rollover.',
      'To calculate the pending rollover, please send your username or registered phone.',
      'Share your username or registered phone and we will check how much rollover is still missing.',
    ],
  },
  rollover_dispute_explain: {
    es: [
      'Entiendo su duda. El rollover se calcula automáticamente con los registros de apuesta del sistema; por eso puede tardar en reflejarse si las jugadas aún no actualizan. Si el monto sigue igual después de seguir jugando, le paso con atención humana para revisar el detalle.',
      'Comprendo. El monto de rollover baja según las apuestas válidas registradas por el sistema. Si usted ya jugó y el valor no cambia, puedo pasarle con un agente para que revise el cálculo.',
    ],
    zh: [
      '我理解你的疑問。流水會依照系統實際記錄到的有效投注自動計算；如果你已經繼續投注但金額仍沒有變，我會幫你轉真人客服核對明細。',
      '了解，流水金額會依系統記錄到的有效投注下降。如果你已經玩了但數字還是一樣，我可以幫你轉接真人客服確認計算結果。',
    ],
    en: [
      'I understand your concern. Rollover is calculated automatically from valid wagers recorded by the system. If you already played and the amount still does not change, I can transfer you to an agent to check the details.',
      'Understood. The rollover amount decreases based on valid bets recorded by the system. If you already played and the number is still the same, I can transfer you to live support to review the calculation.',
    ],
  },
  screenshot_upload_tutorial: {
    es: 'Para enviar una captura:\n\n1. Toque el botón «+» o el icono de adjuntar en el chat.\n2. Elija la imagen desde su galería.\n3. Envíela aquí en la conversación.\n\nPor favor envíe la captura donde se vea claramente el comprobante, la solicitud o el error.',
    zh: '若要上傳截圖：\n\n1. 點聊天室裡的「+」或附件按鈕。\n2. 從相簿選擇圖片。\n3. 直接傳送到這個聊天室。\n\n請上傳能清楚看到付款憑證、提款申請或錯誤畫面的截圖。',
    en: 'To send a screenshot:\n\n1. Tap the “+” button or attachment icon in the chat.\n2. Choose the image from your gallery.\n3. Send it here in the conversation.\n\nPlease send a clear screenshot showing the receipt, request, or error.',
  },
  forgot_password: {
    es: 'En la mayoría de los casos, el usuario es el teléfono registrado. Para restablecer su contraseña, siga los pasos en la imagen. Si después aún no puede ingresar, envíenos la captura del error.',
    zh: '多數情況下，用戶名是註冊手機號。請依照圖片步驟重設密碼；若仍無法登入，請傳錯誤截圖。',
    en: 'In most cases, the username is the registered phone number. To reset your password, follow the steps in the image. If you still cannot log in, please send a screenshot of the error.',
  },
  pending_reply_ask_identity: {
    es: [
      'Envíe usuario, teléfono o e-mail del caso anterior para orientarle con el siguiente paso.',
      'Para ubicar su caso anterior, envíe el usuario, teléfono registrado o e-mail usado en ese caso.',
      'Compártanos el usuario, teléfono o e-mail del caso anterior y revisaremos cómo continuar.',
    ],
    zh: [
      '請提供上一筆案件使用的用戶名、註冊手機號或 email，我們會引導你下一步怎麼處理。',
      '為了查找上一筆案件，請提供當時使用的用戶名、註冊手機號或 email。',
      '請傳上一筆案件的用戶名、手機號或 email，我們會確認接下來怎麼處理。',
    ],
    en: [
      'Please send the username, registered phone number, or email from the previous case so we can guide your next step.',
      'To locate your previous case, send the username, registered phone, or email used in that case.',
      'Share the username, phone, or email from the previous case and we will check how to continue.',
    ],
  },
  pending_reply_invalid_identity: {
    es: [
      'Por favor envíe solo su nombre de usuario, teléfono registrado o e-mail para ubicar mejor el caso.',
      'Necesito solo un dato para buscarlo: usuario, teléfono registrado o e-mail.',
      'Para evitar confusión, envíe únicamente el usuario, teléfono registrado o e-mail del caso.',
    ],
    zh: [
      '請只輸入用戶名、註冊手機號或 email，方便我們判斷上一筆案件。',
      '這一步只需要一個資料：用戶名、註冊手機號或 email。',
      '為了避免查錯，請只傳上一筆案件的用戶名、手機號或 email。',
    ],
    en: [
      'Please send only your username, registered phone number, or email so we can identify the case more accurately.',
      'I only need one detail to search: username, registered phone, or email.',
      'To avoid confusion, please send only the username, registered phone, or email from the case.',
    ],
  },
  pending_reply_not_found: {
    es: [
      'No encontré un caso anterior activo con esos datos. Para que no se detenga su atención, elija una opción del menú o atención humana.',
      'Con esos datos no veo un caso activo. Para continuar, seleccione el tipo de problema en el menú o atención humana.',
      'No encontré una consulta abierta con esa información. Elija una opción del menú para iniciar el camino correcto.',
    ],
    zh: [
      '目前沒有找到這組資料的上一筆有效案件。為了避免你的問題停住，請從選單選擇問題類型，或選真人客服。',
      '用這組資料沒有查到進行中的案件。請從選單選擇問題類型，或選真人客服繼續。',
      '目前沒有找到對應的上一筆案件。請重新從選單選擇正確問題，讓我們繼續協助。',
    ],
    en: [
      'I did not find an active previous case with those details. To keep your support moving, please choose a menu option or live support.',
      'I do not see an active case with those details. Please choose the issue type from the menu or live support.',
      'I did not find an open inquiry with that information. Please choose a menu option to continue correctly.',
    ],
  },
  pending_reply_case_waiting: {
    es: [
      'Su caso anterior figura como registrado con esos datos. Aún está en revisión y todavía no hay una respuesta final. Cuando el equipo responda, le avisaremos aquí.',
      'Encontré su caso anterior. Sigue en revisión, así que todavía debemos esperar la respuesta del equipo.',
      'El caso anterior está registrado y continúa pendiente de respuesta. Apenas haya novedad, se lo informaremos aquí.',
    ],
    zh: [
      '我們已經有你上一筆案件紀錄，目前仍在審查中，還沒有最終答覆。後台回覆後，我們會在這裡通知你。',
      '已找到你的上一筆案件，目前還在確認中，需要等待團隊回覆。',
      '上一筆案件已登記，目前仍等待回覆；有新進度會在這裡通知你。',
    ],
    en: [
      'Your previous case appears to be registered with those details. It is still under review and there is no final answer yet. Once the team replies, we will notify you here.',
      'I found your previous case. It is still under review, so we need to wait for the team reply.',
      'The previous case is registered and still waiting for a reply. We will update you here when there is news.',
    ],
  },
  pending_reply_human_handoff: {
    es: [
      'Encontré un caso anterior con esos datos y ya fue derivado a atención humana. Si sigue siendo el mismo caso, espere al agente; si es otro problema, elija una opción del menú.',
      'Ese caso ya está con atención humana. Si es el mismo tema, por favor espere al agente; si es otro, vuelva al menú.',
      'La consulta anterior ya fue pasada a un agente. Para el mismo caso, espere la atención; para un tema nuevo, elija una opción del menú.',
    ],
    zh: [
      '我找到一筆相同資料的上一筆案件，已經轉給真人客服。如果還是同一件事，請等待專員；如果是新問題，請從選單選擇。',
      '這筆案件已經由真人客服處理中。若是同一件事，請等專員；若是新問題，請回選單選擇。',
      '上一筆查詢已轉給專員。若仍是同一案件，請等待客服；如果是不同問題，請從選單重新選。',
    ],
    en: [
      'I found a previous case with those details and it has already been sent to live support. If it is the same case, please wait for the agent; if it is a new issue, choose an option from the menu.',
      'That case is already with live support. If it is the same topic, please wait for the agent; if it is another issue, return to the menu.',
      'The previous inquiry was already transferred to an agent. For the same case, please wait; for a new topic, choose a menu option.',
    ],
  },
  pending_reply_found_intro: {
    es: [
      'Encontramos la respuesta de su consulta anterior:',
      'Esta es la última respuesta registrada para su caso anterior:',
      'Encontré una actualización de su consulta anterior:',
    ],
    zh: [
      '已找到你上一筆查詢的回覆：',
      '這是你上一筆案件目前留下的回覆：',
      '我找到上一筆查詢的更新內容：',
    ],
    en: [
      'We found the reply to your previous inquiry:',
      'This is the latest reply recorded for your previous case:',
      'I found an update from your previous inquiry:',
    ],
  },
  need_customer_info_request: {
    es: [
      'Para continuar, por favor envíe los datos del caso: usuario, monto, fecha o referencia.',
      'Para seguir revisando, comparta los datos del caso: usuario, monto, fecha o referencia.',
      'Necesitamos un poco más de información: usuario, monto, fecha o número de referencia.',
    ],
    zh: [
      '為了繼續協助，請補充案件相關資料：帳號、金額、日期或參考號。',
      '要繼續確認，請提供案件資料：帳號、金額、日期或參考號。',
      '我們還需要一點資料：帳號、金額、日期或參考號，才能繼續處理。',
    ],
    en: [
      'To continue, please send the case details: username, amount, date, or reference.',
      'To keep reviewing, please share the case details: username, amount, date, or reference.',
      'We need a little more information: username, amount, date, or reference number.',
    ],
  },
  need_screenshot_request: {
    es: [
      'Para seguir revisando, por favor envíe una captura relacionada con el caso.',
      'Para verificarlo mejor, envíe una captura donde se vea el comprobante, solicitud o error.',
      'Necesitamos una captura clara del caso para poder continuar la revisión.',
    ],
    zh: [
      '為了繼續協助，請提供與此案件相關的截圖。',
      '為了更準確確認，請提供能看到憑證、申請或錯誤畫面的截圖。',
      '我們需要一張清楚的案件截圖，才能繼續幫你確認。',
    ],
    en: [
      'To continue reviewing, please send a screenshot related to this case.',
      'To verify it better, please send a screenshot showing the receipt, request, or error.',
      'We need a clear screenshot of the case so we can continue the review.',
    ],
  },
  username_is_phone: {
    es: [
      'En la mayoría de los casos, el usuario es el teléfono registrado. Pruebe iniciar sesión con ese número.',
      'Normalmente el usuario corresponde al teléfono registrado. Intente ingresar con ese número.',
      'Puede probar con su teléfono registrado como usuario; en muchos casos es el dato de acceso.',
    ],
    zh: [
      '你的用戶名通常就是註冊帳號時用的手機號碼，請用那個號碼登入試試。',
      '多數情況下，用戶名會是註冊手機號，你可以先用該號碼登入。',
      '你可以先試著把註冊手機號當成用戶名登入，很多帳號都是這樣設定。',
    ],
    en: [
      'In most cases, your username is the registered phone number. Please try logging in with that number.',
      'Usually the username is the registered phone number. Please try logging in with that number.',
      'You can try using your registered phone as the username; many accounts use that login detail.',
    ],
  },
  generic_done: {
    es: [
      'Su caso quedó registrado. Le avisaremos aquí cuando haya novedad.',
      'Recibimos el caso y quedó en seguimiento. Le responderemos aquí cuando haya actualización.',
      'Listo, el caso está registrado. Cualquier novedad se la informaremos por este chat.',
    ],
    zh: [
      '收到，案件已登記，有結果會在這裡通知你。',
      '我們已收到並會持續追蹤，有更新會在這裡回覆你。',
      '好的，案件已經登記；有任何新進度會在這個對話通知你。',
    ],
    en: [
      'Received. Your case is registered and we will let you know here when there is news.',
      'We received the case and it is now being followed. We will reply here when there is an update.',
      'Done, the case is registered. Any news will be shared in this chat.',
    ],
  },
  human_done: {
    es: [
      'Entiendo. Le paso con un agente para seguir ayudándole.',
      'Comprendo. Lo derivaré con un agente para que puedan continuar ayudándole.',
      'De acuerdo, le paso con atención humana para revisar su caso con más detalle.',
    ],
    zh: [
      '我了解，現在幫你轉接專員繼續處理，請稍候。',
      '了解，這部分我會幫你轉給真人客服繼續協助。',
      '好的，我幫你轉接專員，讓客服人員進一步確認。',
    ],
    en: [
      'I understand. I’ll transfer you to an agent so we can keep helping.',
      'I understand. I will transfer you to an agent so they can continue helping.',
      'Okay, I will pass you to live support to review the case in more detail.',
    ],
  },
  forwarded_followup_pool: {
    es: [
      'Ya enviamos su caso al equipo correspondiente. Le avisaremos por aquí cuando haya actualización. Su dinero está 100% seguro dentro de nuestro proceso.',
      'Su solicitud quedó registrada para revisión. Le avisaremos aquí cuando haya novedad.',
      'El caso sigue en verificación. Si la demora viene del banco o proveedor de pago, igual le responderemos aquí.',
      'Ya tenemos los datos del caso. Por ahora solo debe esperar la actualización aquí.',
      'Su caso quedó registrado y seguirá en revisión hasta tener una respuesta.',
      'El caso está en cola de revisión. Le avisaremos aquí apenas tengamos una actualización.',
      'Recibimos su mensaje y seguimos atentos a la revisión. Su dinero está 100% seguro dentro de nuestro proceso.',
      'Si sale del chat, puede volver luego. La actualización quedará en esta conversación.',
    ],
    zh: [
      '你的案件已送交對應團隊確認，後續會在這個對話更新你。您的資金在我們的流程底下是百分之百安全的。',
      '你的請求已登記等待確認。有新進度會在這裡通知你。',
      '案件仍在確認中。若延遲來自銀行或支付端，我們仍會在這裡回覆你。',
      '我們已收到案件資料。你目前只需要等待這裡的更新。',
      '你的案件已登記，會持續確認到有回覆為止。',
      '案件已排入確認流程。有更新會在這裡通知你。',
      '你的訊息已收到，我們會繼續留意這筆案件。您的資金在我們的流程底下是百分之百安全的。',
      '如果你先離開聊天室，稍後仍可以回來看更新，回覆會保留在這個對話。',
    ],
    en: [
      'We have sent your case to the corresponding team. We will update you here when there is news. Your money is 100% safe within our process.',
      'Your request is registered for review. We will update you here when there is news.',
      'The case is still being checked. If the delay is from the bank or payment provider, we will still reply here.',
      'We already have the case details. For now, please wait for the update here.',
      'Your case is registered and will remain under review until there is a reply.',
      'The case is in the review queue. We will update you here as soon as we have news.',
      'We received your message and will keep watching the review. Your money is 100% safe within our process.',
      'If you leave the chat, you can return later. The update will remain in this conversation.',
    ],
  },
  forwarded_processing_ack: {
    es: [
      'Gracias, seguimos revisando su caso y le avisaremos aquí.',
      'Entiendo. Su caso sigue en revisión y su dinero está 100% seguro dentro de nuestro proceso.',
      'Recibimos su mensaje. Seguimos atentos a la respuesta del equipo.',
    ],
    zh: [
      '收到，我們會繼續確認案件，有進展會在這裡通知你。',
      '了解，案件仍在審核中，您的資金在我們的流程底下是百分之百安全的。',
      '謝謝補充，我們會留意團隊回覆並在此更新。',
    ],
    en: [
      'Thank you. We are still checking your case and will update you here.',
      'I understand. Your case is under review and your funds are 100% safe within our process.',
      'We received your message and are waiting for the team reply.',
    ],
  },
};

function getMessage(key, lang = 'es', variantIndex = 0) {
  const entry = FLOW_MESSAGES[key];
  if (!entry) return null;
  const value = entry[lang] || entry.es;
  if (Array.isArray(value)) return value[variantIndex % value.length];
  return value;
}

module.exports = {
  FLOW_MESSAGES,
  getMessage,
};
