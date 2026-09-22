#!/bin/bash
# Тест портала Vegas: маршруты, чат-механика, библиотека, отсутствие JS.
BASE=http://127.0.0.1:8000
PASS=0; FAIL=0
ok(){ if [ "$1" = "$2" ]; then PASS=$((PASS+1)); echo "  OK  $3"; else FAIL=$((FAIL+1)); echo "FAIL  $3 (ожидалось '$1', получено '$2')"; fi }
has(){ if echo "$2" | grep -q "$3"; then PASS=$((PASS+1)); echo "  OK  $1 contains: $3"; else FAIL=$((FAIL+1)); echo "FAIL  $1 missing: $3"; fi }
lacks(){ if echo "$2" | grep -q "$3"; then FAIL=$((FAIL+1)); echo "FAIL  $1 must NOT contain: $3"; else PASS=$((PASS+1)); echo "  OK  $1 clean: $3"; fi }

echo "=== 1. Редирект / -> /search ==="
code=$(curl -s -o /dev/null -w "%{http_code}" $BASE/)
ok "302" "$code" "GET / -> 302"
loc=$(curl -s -o /dev/null -w "%{redirect_url}" $BASE/)
has "Location" "$loc" "/search"

echo "=== 2. Поиск ==="
code=$(curl -s -o /tmp/search.html -w "%{http_code}" $BASE/search)
ok "200" "$code" "GET /search"
has "/search" "$(cat /tmp/search.html)" "name=\"q\""
has "шапка" "$(cat /tmp/search.html)" 'class="hdr"'
has "шапка чёрная" "$(cat /tmp/search.html)" '.hdr{background:#000'
has "brand Vegas" "$(cat /tmp/search.html)" 'Vegas'

echo "=== 3. Чат: вход по нику ==="
code=$(curl -s -o /tmp/chat0.html -w "%{http_code}" $BASE/chat)
ok "200" "$code" "GET /chat (без ника)"
has "chat0" "$(cat /tmp/chat0.html)" 'name="action" value="nick"'

code=$(curl -s -o /dev/null -D /tmp/hdr.txt -w "%{http_code}" -d "action=nick&n=Тестер" $BASE/chat)
ok "302" "$code" "POST /chat (установка ника)"
grep -qi "set-cookie: nick=" /tmp/hdr.txt && { PASS=$((PASS+1)); echo "  OK  Set-Cookie nick"; } || { FAIL=$((FAIL+1)); echo "FAIL  нет Set-Cookie nick"; }

echo "=== 4. Чат: главная страница с iframe ==="
curl -s -o /tmp/chat1.html "$BASE/chat?nick=%D0%A2%D0%B5%D1%81%D1%82%D0%B5%D1%80"
has "chat1" "$(cat /tmp/chat1.html)" '<iframe src="/chat/frame?nick='
has "chat1" "$(cat /tmp/chat1.html)" 'target="chat_frame"'
has "chat1" "$(cat /tmp/chat1.html)" 'action="/chat/send?nick='
grep -qi 'meta http-equiv="refresh"' /tmp/chat1.html && { FAIL=$((FAIL+1)); echo "FAIL  главная /chat НЕ должна автообновляться!"; } || { PASS=$((PASS+1)); echo "  OK  на /chat нет meta refresh (ввод не ломается)"; }

echo "=== 5. Чат: отправка и порядок (новые СВЕРХУ) ==="
code=$(curl -s -o /dev/null -w "%{http_code}" -d "x=ПЕРВОЕ+старое+сообщение" "$BASE/chat/send?nick=%D0%A2%D0%B5%D1%81%D1%82%D0%B5%D1%80")
ok "302" "$code" "POST /chat/send (первое)"
curl -s -o /dev/null -d "x=ВТОРОЕ+свежее" "$BASE/chat/send?nick=%D0%A2%D0%B5%D1%81%D1%82%D0%B5%D1%80"
curl -s -o /tmp/frame.html "$BASE/chat/frame?page=1&nick=%D0%A2%D0%B5%D1%81%D1%82%D0%B5%D1%80"
has "frame" "$(cat /tmp/frame.html)" "ВТОРОЕ свежее"
pos2=$(echo "$(cat /tmp/frame.html)" | grep -bo "ВТОРОЕ свежее" | head -1 | cut -d: -f1)
pos1=$(echo "$(cat /tmp/frame.html)" | grep -bo "ПЕРВОЕ старое сообщение" | head -1 | cut -d: -f1)
if [ -n "$pos2" ] && [ -n "$pos1" ] && [ "$pos2" -lt "$pos1" ]; then PASS=$((PASS+1)); echo "  OK  новые сообщения сверху"; else FAIL=$((FAIL+1)); echo "FAIL  порядок сообщений неверный ($pos2 >= $pos1)"; fi
has "frame" "$(cat /tmp/frame.html)" 'http-equiv="refresh"'

echo "=== 6. Чат: просмотр работает без кук (старый телефон) ==="
curl -s -o /tmp/frame2.html "$BASE/chat/frame"
has "frame-no-cookie" "$(cat /tmp/frame2.html)" "ВТОРОЕ свежее"

echo "=== 7. Чат: версия без фреймов ==="
code=$(curl -s -o /tmp/simple.html -w "%{http_code}" "$BASE/chat/simple?nick=%D0%A2%D0%B5%D1%81%D1%82%D0%B5%D1%80")
ok "200" "$code" "GET /chat/simple"
has "simple" "$(cat /tmp/simple.html)" "back\" value=\"simple"
if grep -qi 'http-equiv="refresh"' /tmp/simple.html; then FAIL=$((FAIL+1)); echo "FAIL  simple: авто-refresh сломает ввод"; else PASS=$((PASS+1)); echo "  OK  simple без автообновления"; fi

echo "=== 8. Загрузки: список и закачка ==="
code=$(curl -s -o /tmp/dl.html -w "%{http_code}" $BASE/downloads)
ok "200" "$code" "GET /downloads"
echo "тестовое содержимое" > /tmp/upload.txt
curl -s -o /dev/null -F "f=@/tmp/upload.txt" $BASE/downloads
[ -f data/downloads/upload.txt ] && { PASS=$((PASS+1)); echo "  OK  файл закачан"; } || { FAIL=$((FAIL+1)); echo "FAIL  файл не закачан"; }
code=$(curl -s -o /tmp/dlfile.txt -w "%{http_code}" "$BASE/dl?n=upload.txt")
ok "200" "$code" "GET /dl"
has "dlfile" "$(cat /tmp/dlfile.txt)" "тестовое содержимое"

echo "=== 9. Архив ==="
curl -s -o /dev/null -d "n=Заметка&x=Текст+заметки" $BASE/archive/save
[ -f data/archive/Заметка.txt ] && { PASS=$((PASS+1)); echo "  OK  запись создана"; } || { FAIL=$((FAIL+1)); echo "FAIL  запись не создана"; }
curl -s -o /tmp/arc.html "$BASE/archive"
has "archive" "$(cat /tmp/arc.html)" "Заметка.txt"
curl -s -o /tmp/arcredirect.html -G --data-urlencode "n=Заметка.txt" "$BASE/archive/view"
has "view" "$(cat /tmp/arcredirect.html)" "Текст заметки"
curl -s -o /dev/null -d "n=Заметка.txt" $BASE/archive/del
[ ! -f "data/archive/Заметка.txt" ] && { PASS=$((PASS+1)); echo "  OK  запись удалена"; } || { FAIL=$((FAIL+1)); echo "FAIL  запись не удалена"; }

echo "=== 10. Вега ==="
code=$(curl -s -o /tmp/vega.html -w "%{http_code}" $BASE/vega)
ok "200" "$code" "GET /vega"
has "vega" "$(cat /tmp/vega.html)" 'action="/vega/send?sid='

echo "=== 11. Библиотека (локальная) ==="
code=$(curl -s -o /tmp/library.html -w "%{http_code}" $BASE/library)
ok "200" "$code" "GET /library"

echo "=== 12. Флибуста: поиск и сохранение в библиотеку (внешние; до 3 мин) ==="
code=$(curl -s -o /tmp/flb.html -w "%{http_code}" --max-time 120 "$BASE/flibusta?q=%D0%92%D0%B0%D0%B9%D0%BD%D0%B8%D1%85")
ok "200" "$code" "GET /flibusta?q=..."
has "flb" "$(cat /tmp/flb.html)" "в библиотеку"
lacks "flb" "$(cat /tmp/flb.html)" "\.epub\|\.pdf\|/books/get\|fmt=fb2"
# сохраняем известную книгу (id 522114) — должен быть редирект в читалку
loc=$(curl -s -o /dev/null -w "%{redirect_url}" --max-time 180 "$BASE/flibusta/save?b=522114")
has "save redirect" "$loc" "/library/read?b="
if echo "$loc" | grep -q "/library/read?b="; then
  BID=$(echo "$loc" | sed 's/.*b=//')
  curl -s -o /tmp/book1.html --max-time 60 "$BASE/library/read?b=$BID&page=1"
  has "read page" "$(cat /tmp/book1.html)" "стр. 1/"
  lacks "read page чистый текст" "$(cat /tmp/book1.html)" "\[рис:\|Книжное братство\|booksearch\|/reader?url="
  code=$(curl -s -o /tmp/libsearch.html -w "%{http_code}" -G --data-urlencode "q=Достать чернил" "$BASE/library")
  has "поиск по библиотеке" "$(cat /tmp/libsearch.html)" "<b>Достать чернил"
  # постраничность: вторая страница отличается от первой
  curl -s -o /tmp/book2.html --max-time 60 "$BASE/library/read?b=$BID&page=2"
  has "read page 2" "$(cat /tmp/book2.html)" "стр. 2/"
  curl -s -o /dev/null -d "b=$BID" $BASE/library/del
  [ ! -f "data/library/$BID.txt" ] && { PASS=$((PASS+1)); echo "  OK  книга удалена"; } || { FAIL=$((FAIL+1)); echo "FAIL  книга не удалена"; }
fi

echo "=== 13. Читалка чужих страниц ==="
code=$(curl -s -o /tmp/reader.html -w "%{http_code}" --max-time 30 "$BASE/reader?url=https%3A%2F%2Fexample.com&imgs=0")
ok "200" "$code" "GET /reader?url=example.com"
has "reader" "$(cat /tmp/reader.html)" "Example Domain"

echo "=== 14. Никакого JavaScript ==="
JS=0
for f in /tmp/search.html /tmp/chat0.html /tmp/chat1.html /tmp/frame.html /tmp/simple.html /tmp/dl.html /tmp/arc.html /tmp/vega.html /tmp/library.html /tmp/book1.html; do
  [ -f "$f" ] || continue
  if grep -qi "<script" "$f"; then JS=1; echo "FAIL  $f содержит <script>"; fi
  if grep -qi "javascript:" "$f"; then JS=1; echo "FAIL  $f содержит javascript:"; fi
  if grep -qi "onclick\|onload\|onsubmit" "$f"; then JS=1; echo "FAIL  $f содержит inline-обработчики"; fi
done
[ $JS -eq 0 ] && { PASS=$((PASS+1)); echo "  OK  ни <script>, ни javascript:, ни inline-обработчиков"; }

echo "=== 15. Размеры страниц (важно для GPRS) ==="
for f in /tmp/chat1.html /tmp/frame.html /tmp/search.html /tmp/library.html; do
  [ -f "$f" ] || continue
  size=$(wc -c < "$f")
  echo "  $(basename $f): ${size} байт"
  [ "$size" -lt 15000 ] && { PASS=$((PASS+1)); } || { FAIL=$((FAIL+1)); echo "FAIL  $f больше 15КБ"; }
done

echo "=== 16. 404 и HEAD ==="
code=$(curl -s -o /dev/null -w "%{http_code}" $BASE/nonexistent)
ok "404" "$code" "GET /nonexistent -> 404"
code=$(curl -s -o /dev/null -w "%{http_code}" -I $BASE/chat)
ok "200" "$code" "HEAD /chat"
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/books")
ok "404" "$code" "старый маршрут /books больше не существует"

echo ""
echo "================================"
echo "ПРОЙДЕНО: $PASS   ПРОВАЛЕНО: $FAIL"
