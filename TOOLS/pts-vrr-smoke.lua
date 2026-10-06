-- Active-playback smoke test. Pass with --script=TOOLS/pts-vrr-smoke.lua.
-- Use a seekable clip >=3 seconds, --no-config, --video-sync=pts, and (for a
-- headless simulated display) --vo=null --vo-null-fps=144. Does not prove VRR.
local mp = require 'mp'
local completed = false

local function fail(message)
    mp.msg.error('PTS-SMOKE FAIL: ' .. message)
    mp.commandv('quit', '4')
end

local function check(value, message)
    if not value then fail(message); return false end
    return true
end

local function later(delay, callback)
    mp.add_timeout(delay, callback)
end

mp.register_event('file-loaded', function()
    later(0.15, function() mp.set_property('video-sync', 'audio') end)
    later(0.30, function() mp.set_property('video-sync', 'display-resample') end)
    later(0.55, function()
        if not check(mp.get_property_native('display-sync-active'),
                     'display-sync did not activate') then return end
        mp.set_property('video-sync', 'pts')
    end)
    later(0.80, function()
        if not check(not mp.get_property_native('display-sync-active'),
                     'display-sync stayed active after switching to pts') then return end
        local a = mp.get_property_number('audio-speed-correction', 1)
        local v = mp.get_property_number('video-speed-correction', 1)
        if not check(math.abs(a - 1) < 0.000001 and math.abs(v - 1) < 0.000001,
                     'speed correction was not restored') then return end
        mp.set_property_native('pause', true)
    end)
    later(0.95, function()
        if not check(mp.get_property_native('pause'), 'pause failed') then return end
        mp.commandv('frame-step')
    end)
    later(1.10, function()
        if not check(mp.get_property_native('pause'), 'frame-step unpaused') then return end
        mp.commandv('seek', '0.4', 'absolute+exact')
        mp.set_property_number('speed', 2)
        mp.set_property_native('pause', false)
    end)
    later(1.35, function()
        if not check(mp.get_property('video-sync') == 'pts', 'mode lost after seek') then return end
        if not check(not mp.get_property_native('pause'), 'resume failed') then return end
        if not check(mp.get_property_number('speed') == 2, 'speed change failed') then return end
        completed = true
        mp.msg.info('PTS-SMOKE controls passed; waiting for EOF')
    end)
end)

mp.register_event('end-file', function(event)
    if not completed or event.reason ~= 'eof' then
        fail('premature end-file or non-EOF termination')
        return
    end
    mp.msg.info('PTS-SMOKE PASS: audio/display/pts transitions, pause, step, seek, speed, EOF')
end)

later(15, function() fail('test timed out') end)
