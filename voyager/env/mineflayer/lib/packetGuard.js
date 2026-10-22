// fixes for packets mineflayer 4.8.1 gets wrong, applied to every outgoing packet

function inject(bot) {
    // mineflayer 4.8.1 sends window_click changedSlots as bare slot numbers, which the
    // installed protocol cannot serialize; newer mineflayer maps them to {location, item}
    const Item = require("prismarine-item")(bot.registry);
    const write = bot._client.write.bind(bot._client);
    const lastValid = {
        x: bot.entity.position.x,
        y: bot.entity.position.y,
        z: bot.entity.position.z,
        // packet angles are in degrees, filled in by the first valid packet
        yaw: 0,
        pitch: 0,
    };
    let lastInvalidLog = 0;
    // once mineflayer's internal lastSentYaw is NaN it stays NaN until the server teleports
    // the bot, so refuse NaN look targets and reset the look through a forced look
    let safeLook = {
        yaw: Number.isFinite(bot.entity.yaw) ? bot.entity.yaw : 0,
        pitch: Number.isFinite(bot.entity.pitch) ? bot.entity.pitch : 0,
    };
    bot.on("physicsTick", () => {
        if (Number.isFinite(bot.entity.yaw) && Number.isFinite(bot.entity.pitch)) {
            safeLook = { yaw: bot.entity.yaw, pitch: bot.entity.pitch };
        }
    });
    const look = bot.look;
    bot.look = async (yaw, pitch, force) => {
        if (!Number.isFinite(yaw) || !Number.isFinite(pitch)) return;
        return look(yaw, pitch, force);
    };
    function resetLook() {
        // a forced look only sets lastSentYaw when the look actually changes
        bot.entity.yaw = safeLook.yaw + 1;
        bot.entity.pitch = safeLook.pitch;
        look(safeLook.yaw, safeLook.pitch, true);
    }
    bot._client.write = (name, params) => {
        // physics occasionally produces NaN, and the server kicks for invalid_player_movement.
        // Repair the packet with the last valid values instead of dropping it: a client that
        // sends nothing for 30 seconds is disconnected, which while paused nobody can undo
        if (["position", "position_look", "look"].includes(name)) {
            const keys = ["x", "y", "z", "yaw", "pitch"].filter((key) => key in params);
            const invalid = keys.filter((key) => !Number.isFinite(params[key]));
            if (invalid.length > 0) {
                if (Date.now() - lastInvalidLog > 5000) {
                    lastInvalidLog = Date.now();
                    console.log(`Repaired invalid ${name} packet: ${JSON.stringify(params)}`);
                }
                params = { ...params };
                invalid.forEach((key) => (params[key] = lastValid[key]));
                if (invalid.some((key) => ["x", "y", "z"].includes(key))) {
                    bot.entity.position.set(lastValid.x, lastValid.y, lastValid.z);
                    bot.entity.velocity.set(0, 0, 0);
                }
                if (invalid.includes("yaw") || invalid.includes("pitch")) resetLook();
            }
            keys.forEach((key) => (lastValid[key] = params[key]));
        }
        if (name === "window_click" && Array.isArray(params.changedSlots)) {
            const window = bot.currentWindow || bot.inventory;
            params.changedSlots = params.changedSlots.map((slot) =>
                typeof slot === "number"
                    ? { location: slot, item: Item.toNotch(window.slots[slot]) }
                    : slot
            );
        }
        return write(name, params);
    };
}

module.exports = { inject };
