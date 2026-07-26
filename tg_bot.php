<?php
/**
 * Telegram Bot - Smart Offer Completion Pipeline
 * Vmtrk → AdPropel → Adsphire → Panel with auto-retry + completion check
 * Made by Slayer
 *
 * Usage:
 *   1. Get bot token from @BotFather on Telegram
 *   2. Set BOT_TOKEN below
 *   3. Run: php tg_bot.php
 */

// ======================================================================
// CONFIGURATION
// ======================================================================
define('BOT_TOKEN', '');           // <-- PUT YOUR BOT TOKEN HERE
define('MAX_RETRIES', 30);         // Max attempts before giving up
define('RETRY_DELAY_SEC', 3);      // Seconds between retries
define('POLLING_TIMEOUT', 25);     // Long-poll timeout in seconds
define('PANEL_URL', 'https://mr4u.iceiy.com/?id=152535&i=1');

// Keywords that indicate the offer is already completed
$COMPLETION_KEYWORDS = [
    'already completed this offer',
    'you have already completed',
    'offer already completed',
    'you\'ve already completed',
    'already completed',
    'already claimed this',
    'offer completed',
    'already claimed',
    'completed this offer',
    'already finished this',
    'you already completed',
    'this offer is completed',
    'already done',
];

// ======================================================================
// TELEGRAM API HELPERS
// ======================================================================
function tgApi($method, $params = []) {
    $url = "https://api.telegram.org/bot" . BOT_TOKEN . "/{$method}";
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST           => true,
        CURLOPT_POSTFIELDS     => $params,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_TIMEOUT        => 20,
    ]);
    $res = curl_exec($ch);
    $err = curl_error($ch);
    curl_close($ch);
    if ($err) {
        logMsg("Telegram API Error: {$err}");
        return null;
    }
    return json_decode($res, true);
}

function sendMessage($chatId, $text) {
    // Split long messages (>4096 chars, Telegram limit)
    if (mb_strlen($text) > 4000) {
        $chunks = mb_str_split($text, 3900);
        foreach ($chunks as $chunk) {
            tgApi('sendMessage', ['chat_id' => $chatId, 'text' => $chunk]);
        }
        return;
    }
    return tgApi('sendMessage', ['chat_id' => $chatId, 'text' => $text]);
}

function editMessage($chatId, $msgId, $text) {
    return tgApi('editMessageText', [
        'chat_id'    => $chatId,
        'message_id' => $msgId,
        'text'       => $text,
    ]);
}

function logMsg($msg) {
    echo "[" . date('Y-m-d H:i:s') . "] {$msg}\n";
}

// ======================================================================
// URL PARSER - Splits two concatenated URLs (no space between them)
// ======================================================================
function parseTwoLinks($text) {
    $text = trim($text);
    // Split on http:// or https:// boundaries
    $parts = preg_split('/(?=https?:\/\/)/', $text, -1, PREG_SPLIT_NO_EMPTY);
    $urls = [];
    foreach ($parts as $part) {
        $part = trim($part);
        if (preg_match('/^https?:\/\//', $part)) {
            $urls[] = $part;
        }
    }
    if (count($urls) >= 2) {
        return [
            'vmtrk_link' => $urls[0],
            'check_link' => $urls[1],
        ];
    }
    return null;
}

// ======================================================================
// PIPELINE: Vmtrk → AdPropel → Adsphire → Panel
// ======================================================================
function runPipeline($vmtrkLink) {
    $userAgent = "Mozilla/5.0 (Linux; Android 16; V2437 Build/BP2A.250605.031.A3_V000L1) AppleWebKit/537.36 (KHTML, like Gecko) Utgmqff/4.0 Chrome/149.0.7827.159 Mobile Safari/537.36";
    $userIp = '103.156.19.178'; // Default fallback IP

    // --- STAGE 1: vmtrk.com → AdPropel redirect ---
    $ch1 = curl_init($vmtrkLink);
    curl_setopt_array($ch1, [
        CURLOPT_RETURNTRANSFER  => true,
        CURLOPT_HEADER          => true,
        CURLOPT_FOLLOWLOCATION  => false,
        CURLOPT_SSL_VERIFYPEER  => false,
        CURLOPT_TIMEOUT         => 15,
        CURLOPT_USERAGENT       => $userAgent,
        CURLOPT_ENCODING        => "",
    ]);
    curl_setopt($ch1, CURLOPT_HTTPHEADER, [
        'sec-ch-ua: "Android WebView";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
        'sec-ch-ua-mobile: ?1',
        'sec-ch-ua-platform: "Android"',
        'upgrade-insecure-requests: 1',
        'x-requested-with: com.mycompany.app.soulbrowser',
        'sec-fetch-site: cross-site',
        'sec-fetch-mode: navigate',
        'sec-fetch-dest: document',
        'referer: https://rewardtk.com/',
        'accept-language: en-US,en;q=0.9',
        "X-Forwarded-For: {$userIp}",
        "X-Real-IP: {$userIp}",
        "Client-IP: {$userIp}",
    ]);

    $res1 = curl_exec($ch1);
    $httpCode1 = curl_getinfo($ch1, CURLINFO_HTTP_CODE);
    $adPropelUrl = curl_getinfo($ch1, CURLINFO_REDIRECT_URL);
    curl_close($ch1);

    // Fallback extraction
    if (empty($adPropelUrl) && $res1) {
        if (preg_match('/location:\s*([^\s\r\n]+)/i', $res1, $m)) {
            $adPropelUrl = trim($m[1]);
        } elseif (preg_match('/<a\s+href=["\']([^"\']+)["\']/i', $res1, $m)) {
            $adPropelUrl = trim($m[1]);
        }
    }
    $adPropelUrl = html_entity_decode((string)$adPropelUrl);

    if (empty($adPropelUrl) || strpos($adPropelUrl, 'http') !== 0) {
        $raw = substr(trim(preg_replace('/\s+/', ' ', strip_tags((string)$res1))), 0, 80);
        return ['ok' => false, 'stage' => 1, 'http' => $httpCode1, 'msg' => $raw ?: 'No redirect'];
    }

    // --- STAGE 2: AdPropel → Adsphire redirect ---
    $ch2 = curl_init($adPropelUrl);
    curl_setopt_array($ch2, [
        CURLOPT_RETURNTRANSFER  => true,
        CURLOPT_HEADER          => true,
        CURLOPT_FOLLOWLOCATION  => false,
        CURLOPT_SSL_VERIFYPEER  => false,
        CURLOPT_TIMEOUT         => 12,
        CURLOPT_USERAGENT       => $userAgent,
        CURLOPT_HTTP_VERSION    => CURL_HTTP_VERSION_1_1,
        CURLOPT_ENCODING        => "",
    ]);
    curl_setopt($ch2, CURLOPT_HTTPHEADER, [
        'Connection: keep-alive',
        'Upgrade-Insecure-Requests: 1',
        'X-Requested-With: com.mycompany.app.soulbrowser',
        'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language: en-US,en;q=0.9',
        "X-Forwarded-For: {$userIp}",
        "X-Real-IP: {$userIp}",
    ]);

    $res2 = curl_exec($ch2);
    $httpCode2 = curl_getinfo($ch2, CURLINFO_HTTP_CODE);
    $adsphireUrl = curl_getinfo($ch2, CURLINFO_REDIRECT_URL);
    curl_close($ch2);

    if (empty($adsphireUrl) && $res2) {
        if (preg_match('/location:\s*([^\s\r\n]+)/i', $res2, $m)) {
            $adsphireUrl = trim($m[1]);
        } elseif (preg_match('/<a\s+href=["\']([^"\']+)["\']/i', $res2, $m)) {
            $adsphireUrl = trim($m[1]);
        }
    }
    $adsphireUrl = html_entity_decode((string)$adsphireUrl);

    if (empty($adsphireUrl) || strpos($adsphireUrl, 'http') !== 0) {
        $raw = substr(trim(preg_replace('/\s+/', ' ', strip_tags((string)$res2))), 0, 80);
        return ['ok' => false, 'stage' => 2, 'http' => $httpCode2, 'msg' => $raw ?: 'No redirect'];
    }

    // --- STAGE 3: POST final Adsphire link to Panel ---
    $ch3 = curl_init(PANEL_URL);
    curl_setopt_array($ch3, [
        CURLOPT_POST           => true,
        CURLOPT_POSTFIELDS     => http_build_query(['link' => $adsphireUrl, 'submit' => 'SUBMIT']),
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_SSL_VERIFYPEER => false,
        CURLOPT_TIMEOUT        => 12,
    ]);
    curl_setopt($ch3, CURLOPT_HTTPHEADER, [
        "Host: mr4u.iceiy.com",
        "Content-Type: application/x-www-form-urlencoded",
        "User-Agent: {$userAgent}",
        "Cookie: __test=0e3273fa003e3c50fee09c3b59d1cc77",
        "Origin: https://mr4u.iceiy.com",
        "Referer: https://mr4u.iceiy.com/?id=152535&i=1",
        "X-Requested-With: com.mycompany.app.soulbrowser",
    ]);

    curl_exec($ch3);
    $panelHttp = curl_getinfo($ch3, CURLINFO_HTTP_CODE);
    curl_close($ch3);

    if ($panelHttp == 200 || $panelHttp == 302) {
        return ['ok' => true, 'link' => $adsphireUrl];
    } else {
        return ['ok' => false, 'stage' => 3, 'http' => $panelHttp, 'msg' => "Panel POST failed (HTTP {$panelHttp})"];
    }
}

// ======================================================================
// COMPLETION CHECKER - Fetches check link and looks for keywords
// ======================================================================
function isOfferCompleted($checkLink, $keywords) {
    $ch = curl_init($checkLink);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_SSL_VERIFYPEER => false,
        CURLOPT_TIMEOUT        => 15,
        CURLOPT_USERAGENT      => "Mozilla/5.0 (Linux; Android 16; V2437) AppleWebKit/537.36 Chrome/149.0 Mobile Safari/537.36",
    ]);
    $response = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if (!$response || $httpCode >= 500) {
        return null; // null = couldn't check (connection error)
    }

    // Strip HTML tags and decode entities for clean text matching
    $text = strip_tags($response);
    $text = html_entity_decode($text, ENT_QUOTES | ENT_HTML5, 'UTF-8');
    $text = preg_replace('/\s+/', ' ', $text); // Normalize whitespace

    foreach ($keywords as $keyword) {
        if (stripos($text, $keyword) !== false) {
            return true;
        }
    }
    return false;
}

// ======================================================================
// MAIN OFFER PROCESSOR - Loop until completed or max retries
// ======================================================================
function processOffer($chatId, $vmtrkLink, $checkLink) {
    global $COMPLETION_KEYWORDS;

    $statusMsg = sendMessage($chatId, "Starting offer completion...\n\nVmtrk: {$vmtrkLink}\nCheck: {$checkLink}");
    $statusMsgId = $statusMsg['result']['message_id'] ?? null;

    for ($attempt = 1; $attempt <= MAX_RETRIES; $attempt++) {

        // --- Update progress every 5 attempts or on first/last ---
        if ($attempt == 1 || $attempt % 5 == 0 || $attempt == MAX_RETRIES) {
            $progress = "🔄 Attempt {$attempt}/" . MAX_RETRIES . "...";
            if ($statusMsgId) {
                editMessage($chatId, $statusMsgId, $progress);
            }
            logMsg("Chat {$chatId} | Attempt {$attempt}");
        }

        // Step 1: Run pipeline
        $result = runPipeline($vmtrkLink);

        if (!$result['ok']) {
            // Pipeline failed at stage N — wait then retry
            logMsg("Chat {$chatId} | Attempt {$attempt} FAILED at Stage {$result['stage']} (HTTP {$result['http']})");
            sleep(RETRY_DELAY_SEC);
            continue;
        }

        // Step 2: Check if offer is completed
        $completed = isOfferCompleted($checkLink, $COMPLETION_KEYWORDS);

        if ($completed === true) {
            // SUCCESS
            $finalMsg = "✅ OFFER COMPLETED!\n\n"
                      . "Completed on attempt: {$attempt}/" . MAX_RETRIES . "\n"
                      . "Final URL: {$result['link']}";
            sendMessage($chatId, $finalMsg);
            logMsg("Chat {$chatId} | COMPLETED on attempt {$attempt}");
            return;
        }

        if ($completed === null) {
            // Check link unreachable — might be temporary
            logMsg("Chat {$chatId} | Attempt {$attempt} | Check link unreachable, retrying...");
        }

        // Not completed yet — wait then try again
        sleep(RETRY_DELAY_SEC);
    }

    // Exhausted all retries
    $failMsg = "❌ Max retries (" . MAX_RETRIES . ") reached.\n"
             . "Offer did NOT complete. Try again later or check your links.";
    sendMessage($chatId, $failMsg);
    logMsg("Chat {$chatId} | FAILED after " . MAX_RETRIES . " attempts");
}

// ======================================================================
// TELEGRAM BOT MAIN LOOP
// ======================================================================
function runBot() {
    logMsg("Bot starting...");
    if (empty(BOT_TOKEN)) {
        die("ERROR: Set your BOT_TOKEN in tg_bot.php first!\nGet one from @BotFather on Telegram.\n");
    }

    $lastUpdateId = 0;

    while (true) {
        $params = [
            'offset'  => $lastUpdateId + 1,
            'timeout' => POLLING_TIMEOUT,
            'allowed_updates' => json_encode(['message']),
        ];

        $response = tgApi('getUpdates', $params);

        if (!$response || !$response['ok']) {
            logMsg("getUpdates failed, retrying in 5s...");
            sleep(5);
            continue;
        }

        foreach ($response['result'] as $update) {
            $lastUpdateId = $update['update_id'];

            $message = $update['message'] ?? null;
            if (!$message || !isset($message['text'])) continue;

            $chatId = $message['chat']['id'];
            $text = trim($message['text']);

            logMsg("Received from {$chatId}: " . substr($text, 0, 100));

            // /start command
            if ($text === '/start' || stripos($text, '/start') === 0) {
                $help = "🚀 Smart Offer Completion Bot\n\n"
                      . "Send me TWO links in ONE message (no space between them):\n"
                      . "1️⃣ Vmtrk tracking link\n"
                      . "2️⃣ Check link (to verify if offer is complete)\n\n"
                      . "Example:\n"
                      . "https://www.vmtrk.com/click?...https://offers.com/status?...\n\n"
                      . "I will:\n"
                      . "• Run the pipeline once\n"
                      . "• Check if offer completed\n"
                      . "• Retry until done (max " . MAX_RETRIES . " attempts)\n\n"
                      . "Made by Slayer";
                sendMessage($chatId, $help);
                continue;
            }

            // Parse two links
            $links = parseTwoLinks($text);

            if (!$links) {
                sendMessage($chatId, "Please send TWO links in one message (no space).\n\nFirst: Vmtrk link\nSecond: Check link\n\nType /start for help.");
                continue;
            }

            // Process the offer
            processOffer($chatId, $links['vmtrk_link'], $links['check_link']);
        }
    }
}

// ======================================================================
// BOOT
// ======================================================================
runBot();
