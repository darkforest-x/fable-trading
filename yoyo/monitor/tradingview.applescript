-- Owner-authorized UI navigation. URL is data supplied via argv.
property actionDeadline : missing value
property operationStage : "layout"

-- A generic /chart/ link can lose symbol/interval while restoring the saved
-- layout. Keep the owner's verified regional host and concrete layout path.
on savedChartBase(urlText)
    if urlText is missing value then return missing value
    set urlText to urlText as text
    repeat with basePrefix in {"https://cn.tradingview.com/chart/", "https://www.tradingview.com/chart/", "https://tradingview.com/chart/"}
        set prefixText to contents of basePrefix
        if urlText starts with prefixText and (length of urlText) > (length of prefixText) then
            set layoutID to ""
            repeat with c in characters ((length of prefixText) + 1) thru -1 of urlText
                if (contents of c) is in {"/", "?", "#"} then exit repeat
                if (contents of c) is not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789" then return missing value
                set layoutID to layoutID & (contents of c)
            end repeat
            if (length of layoutID) > 0 and (length of layoutID) <= 64 then return prefixText & layoutID & "/"
        end if
    end repeat
    return missing value
end savedChartBase

on bindToLayout(chartURL, layoutBase)
    set savedDelimiters to AppleScript's text item delimiters
    try
        set AppleScript's text item delimiters to "?"
        set urlParts to text items of chartURL
        set AppleScript's text item delimiters to savedDelimiters
        if (count of urlParts) is not 2 then error "SPIKE_INVALID_URL"
        return layoutBase & "?" & item 2 of urlParts
    on error errorMessage number errorNumber
        set AppleScript's text item delimiters to savedDelimiters
        error errorMessage number errorNumber
    end try
end bindToLayout

on checkDeadline()
    if (current date) > actionDeadline then error "SPIKE_DEADLINE"
end checkDeadline

-- Splash/popup windows can disappear between enumerating and reading them.
-- Invalid indices are stale AX references, not evidence of denied permission.
on isTransientAXError(errorNumber)
    return errorNumber is -1719 or errorNumber is -1728
end isTransientAXError

on restoreClipboard(previousClipboard, expectedText)
    try
        with timeout of 3 seconds
            if (the clipboard as text) is expectedText then set the clipboard to previousClipboard
        end timeout
    end try
end restoreClipboard

-- Stop at the first web area: the desktop tab strip, not the chart body.
on firstWebArea(rootElement, depthLeft)
    my checkDeadline()
    tell application "System Events"
        if role of rootElement is "AXWebArea" then return rootElement
        if depthLeft < 1 then return missing value
        my checkDeadline()
        repeat with childElement in UI elements of rootElement
            set foundElement to my firstWebArea(contents of childElement, depthLeft - 1)
            if foundElement is not missing value then return foundElement
        end repeat
    end tell
    return missing value
end firstWebArea

-- Collect only the shallow web areas. Stopping at each AXWebArea avoids walking
-- TradingView's full chart DOM until the verification step needs it.
on collectWebAreas(rootElement, depthLeft)
    my checkDeadline()
    tell application "System Events"
        try
            if role of rootElement is "AXWebArea" then return {rootElement}
            if depthLeft < 1 then return {}
            set foundElements to {}
            repeat with childElement in UI elements of rootElement
                set foundElements to foundElements & my collectWebAreas(contents of childElement, depthLeft - 1)
            end repeat
            return foundElements
        on error errorMessage number errorNumber
            if errorNumber is -1712 or errorMessage is "SPIKE_DEADLINE" then error errorMessage number errorNumber
            return {}
        end try
    end tell
end collectWebAreas

on validSymbol(symbolText)
    if symbolText does not start with "OKX:" then return false
    if symbolText does not end with ".P" then return false
    if (length of symbolText) < 10 or (length of symbolText) > 48 then return false
    repeat with c in characters 5 thru -3 of symbolText
        if (contents of c) is not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" then return false
    end repeat
    return true
end validSymbol

on validInterval(intervalText)
    return intervalText is in {"5", "15", "30", "60", "240", "1D"}
end validInterval

on intervalMatches(descriptionText, intervalText)
    if intervalText is "5" then return descriptionText contains "5 分钟" or descriptionText contains "5 minute"
    if intervalText is "15" then return descriptionText contains "15 分钟" or descriptionText contains "15 minute"
    if intervalText is "30" then return descriptionText contains "30 分钟" or descriptionText contains "30 minute"
    if intervalText is "60" then return descriptionText contains "1 小时" or descriptionText contains "1 hour"
    if intervalText is "240" then return descriptionText contains "4 小时" or descriptionText contains "4 hour"
    if intervalText is "1D" then return descriptionText contains "1 天" or descriptionText contains "1 日" or descriptionText contains "1 day"
    return false
end intervalMatches

-- Window/tab titles can change before the visible chart does. Verify the
-- chart canvas accessibility description itself, which includes both the
-- loaded symbol and interval in current Chinese and English desktop builds.
on loadedChartReady(chartWindow, targetSymbol, targetInterval)
    my checkDeadline()
    tell application "System Events" to tell application process "TradingView"
        try
            -- Opening a clipboard link rebuilds Electron's tab content and can
            -- invalidate the AXWindow object captured before dispatch. Reacquire
            -- only the current foreground window on every poll; never scan a
            -- background window that may already show the requested chart.
            set chartWindow to front window
            set windowSize to size of chartWindow
            if (item 1 of windowSize) < 601 or (item 2 of windowSize) < 301 then return false
            -- Only inspect the window whose menu received this request. Another
            -- open TradingView window may already show the same symbol/period;
            -- it cannot prove that this click changed the active chart.
            set webAreas to my collectWebAreas(contents of chartWindow, 10)
            if (count of webAreas) < 2 then return false
            set chartWebArea to item 2 of webAreas
            set chartElements to entire contents of chartWebArea
            repeat with e in chartElements
                my checkDeadline()
                try
                    if role of e is "AXImage" then
                        set imageText to ""
                        try
                            set imageText to description of e as text
                        end try
                        if imageText contains targetSymbol and my intervalMatches(imageText, targetInterval) then return true
                        try
                            set imageText to name of e as text
                        end try
                        if imageText contains targetSymbol and my intervalMatches(imageText, targetInterval) then return true
                    end if
                end try
            end repeat
        on error errorMessage number errorNumber
            if errorNumber is -1712 or errorMessage is "SPIKE_DEADLINE" then error errorMessage number errorNumber
            return false
        end try
    end tell
    return false
end loadedChartReady

on run argv
    -- A different symbol can take roughly 30–45 seconds to finish loading in
    -- Desktop even though its new tab appears immediately. Keep one bounded
    -- request alive long enough to verify the actual chart canvas.
    set actionDeadline to (current date) + 52
    set operationStage to "layout"
    set previousClipboard to missing value
    set chartURL to missing value
    try
        if (count of argv) is not 3 then error "SPIKE_INVALID_URL"
        set chartURL to item 1 of argv
        set targetSymbol to item 2 of argv
        set targetInterval to item 3 of argv
        if not my validSymbol(targetSymbol) or not my validInterval(targetInterval) then error "SPIKE_INVALID_URL"
        -- This owner preference is outside source code. The generic URL cannot
        -- safely stand in for it because desktop layout restoration drops query.
        set layoutFile to (POSIX path of (path to home folder)) & "Library/Application Support/Fable/ImpulseMonitor/tradingview-layout.txt"
        try
            set layoutBase to my savedChartBase(read (POSIX file layoutFile) as «class utf8»)
        on error
            error "SPIKE_LAYOUT_UNAVAILABLE"
        end try
        if layoutBase is missing value then error "SPIKE_LAYOUT_UNAVAILABLE"
        set chartURL to my bindToLayout(chartURL, layoutBase)
        set operationStage to "clipboard"
        set previousClipboard to the clipboard as record
        -- Keep UI traversal bounded while leaving time to verify the chart
        -- canvas after Desktop consumes the clipboard link.
        with timeout of 54 seconds
            set operationStage to "activate"
            tell application id "com.tradingview.tradingviewapp.desktop" to activate
            tell application "System Events" to tell application process "TradingView"
                my checkDeadline()
                set frontmost to true
                -- Electron enables its full AX tree lazily. Request it once:
                -- repeated writes reset Electron's two-second debounce timer.
                -- https://www.electronjs.org/docs/latest/tutorial/accessibility
                set operationStage to "accessibility"
                set value of attribute "AXManualAccessibility" to true
                set operationStage to "window_ready"
                set chartWindow to missing value
                set chrome to missing value
                -- A cold launch first exposes a small splash window. Wait for
                -- the desktop tab strip instead of failing immediately after
                -- activate. All retries remain inside the operation deadline.
                repeat
                    my checkDeadline()
                    try
                        -- Use the actual foreground chart window. Iterating every
                        -- window can dispatch into a background TradingView window
                        -- and then verify a different one than the user sees.
                        set candidateWindow to front window
                        set s to size of candidateWindow
                        if (item 1 of s) > 600 and (item 2 of s) > 300 then
                            set candidateChrome to my firstWebArea(contents of candidateWindow, 14)
                            if candidateChrome is not missing value then
                                set chartWindow to candidateWindow
                                set chrome to candidateChrome
                                exit repeat
                            end if
                        end if
                    on error errorMessage number errorNumber
                        -- The splash may disappear during AX traversal.
                        -- Permission and timeout errors must still surface.
                        if not my isTransientAXError(errorNumber) then error errorMessage number errorNumber
                    end try
                    delay 0.2
                end repeat
                -- Do not touch the clipboard while merely waiting for launch.
                set operationStage to "clipboard"
                set the clipboard to chartURL
                -- Desktop 3.4.0/3.4.1 exposes the title-bar menu as its rightmost button.
                -- The name changes when an update is available. Use live AX bounds.
                set operationStage to "menu_button"
                set menuButton to missing value
                set rightEdge to -100000
                my checkDeadline()
                set chromePosition to position of chrome
                set chromeTop to item 2 of chromePosition
                my checkDeadline()
                set chromeElements to entire contents of chrome
                repeat with e in chromeElements
                    my checkDeadline()
                    try
                        my checkDeadline()
                        if role of e is "AXButton" then
                            my checkDeadline()
                            set p to position of e
                            my checkDeadline()
                            set s to size of e
                            if (item 2 of p) < chromeTop + 50 and (item 1 of p) + (item 1 of s) > rightEdge then
                                set menuButton to contents of e
                                set rightEdge to (item 1 of p) + (item 1 of s)
                            end if
                        end if
                    on error errorMessage number errorNumber
                        if errorNumber is -1712 or errorMessage is "SPIKE_DEADLINE" then error errorMessage number errorNumber
                    end try
                end repeat
                if menuButton is missing value then error "SPIKE_MENU_UNAVAILABLE"
                -- Close any old popup so clipboard validity is recomputed on open.
                my checkDeadline()
                key code 53
                my checkDeadline()
                click menuButton
            end tell
            set operationStage to "menu_items"
            set didRequest to false
            repeat 12 times
                tell application "System Events" to tell application process "TradingView"
                    my checkDeadline()
                    repeat with w in windows
                        try
                            my checkDeadline()
                            set s to size of w
                            if (item 1 of s) < 600 and (item 2 of s) > 100 then
                                my checkDeadline()
                                set menuElements to entire contents of w
                                repeat with e in menuElements
                                    my checkDeadline()
                                    set isOpenItem to false
                                    try
                                        my checkDeadline()
                                        set itemName to name of e
                                        set isOpenItem to itemName is "从剪贴板打开链接" or itemName is "Open link from clipboard"
                                on error errorMessage number errorNumber
                                    if errorNumber is -1712 or errorMessage is "SPIKE_DEADLINE" then error errorMessage number errorNumber
                                end try
                                if isOpenItem then
                                    my checkDeadline()
                                    if (the clipboard as text) is not chartURL then error "SPIKE_CLIPBOARD_CHANGED"
                                    my checkDeadline()
                                    set operationStage to "dispatch"
                                    click e
                                    set didRequest to true
                                    exit repeat
                                end if
                            end repeat
                        end if
                        if didRequest then exit repeat
                        on error errorMessage number errorNumber
                            -- Retry only observation. Never retry a dispatched click.
                            if operationStage is "dispatch" or not my isTransientAXError(errorNumber) then error errorMessage number errorNumber
                        end try
                    end repeat
                end tell
                if didRequest then exit repeat
                delay 0.2
            end repeat
            if not didRequest then error "SPIKE_MENU_UNAVAILABLE"
            -- Clipboard consumption is asynchronous in the desktop app.
            my checkDeadline()
            delay 2
            set operationStage to "verify_chart"
            set chartVerified to false
            -- Desktop can switch the tab title several seconds before the chart
            -- canvas finishes loading. Keep polling the canvas identity so a
            -- valid slow load is not reported as a mismatch to the card UI.
            repeat 40 times
                if my loadedChartReady(chartWindow, targetSymbol, targetInterval) then
                    set chartVerified to true
                    exit repeat
                end if
                delay 1
            end repeat
            if not chartVerified then error "SPIKE_CHART_MISMATCH"
        end timeout
        my restoreClipboard(previousClipboard, chartURL)
        return "loaded"
    on error errorMessage number errorNumber
        if previousClipboard is not missing value then my restoreClipboard(previousClipboard, chartURL)
        set errorReason to "SPIKE_AX_UNAVAILABLE"
        repeat with knownReason in {"SPIKE_LAYOUT_UNAVAILABLE", "SPIKE_CLIPBOARD_CHANGED", "SPIKE_DEADLINE", "SPIKE_MENU_UNAVAILABLE", "SPIKE_CHART_MISMATCH", "SPIKE_INVALID_URL"}
            if errorMessage contains (contents of knownReason) then set errorReason to contents of knownReason
        end repeat
        error "SPIKE_STAGE=" & operationStage & ";SPIKE_CODE=" & errorNumber & ";SPIKE_REASON=" & errorReason number errorNumber
    end try
end run
