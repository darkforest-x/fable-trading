-- Owner-authorized UI navigation. URL is data supplied via argv.
property actionDeadline : missing value

on checkDeadline()
    if (current date) > actionDeadline then error "SPIKE_DEADLINE"
end checkDeadline

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

on run argv
    set actionDeadline to (current date) + 15
    set chartURL to item 1 of argv
    set previousClipboard to the clipboard as record
    try
        -- Per-event timeout allows cleanup before the Python process deadline.
        with timeout of 3 seconds
            tell application id "com.tradingview.tradingviewapp.desktop" to activate
            set the clipboard to chartURL
            tell application "System Events" to tell application process "TradingView"
                my checkDeadline()
                set frontmost to true
                set chartWindow to missing value
                my checkDeadline()
                repeat with w in windows
                    my checkDeadline()
                    my checkDeadline()
                    set s to size of w
                    if (item 1 of s) > 600 and (item 2 of s) > 300 then
                        set chartWindow to contents of w
                        exit repeat
                    end if
                end repeat
                if chartWindow is missing value then error "SPIKE_WINDOW_UNAVAILABLE"
                set chrome to my firstWebArea(chartWindow, 14)
                if chrome is missing value then error "SPIKE_CHROME_UNAVAILABLE"
                -- Desktop 3.4.0 exposes the title-bar menu as its rightmost button.
                -- The name changes when an update is available. Use live AX bounds.
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
            set didRequest to false
            repeat 12 times
            tell application "System Events" to tell application process "TradingView"
                    my checkDeadline()
                    repeat with w in windows
                        my checkDeadline()
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
                                    click e
                                    set didRequest to true
                                    exit repeat
                                end if
                            end repeat
                        end if
                        if didRequest then exit repeat
                    end repeat
                end tell
                if didRequest then exit repeat
                delay 0.2
            end repeat
            if not didRequest then error "SPIKE_MENU_UNAVAILABLE"
            -- Clipboard consumption is asynchronous in the desktop app.
            my checkDeadline()
            delay 1
        end timeout
        my restoreClipboard(previousClipboard, chartURL)
        return "requested"
    on error errorMessage number errorNumber
        my restoreClipboard(previousClipboard, chartURL)
        error errorMessage number errorNumber
    end try
end run
