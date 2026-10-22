const fs = require("fs");
const express = require("express");
const bodyParser = require("body-parser");
const mineflayer = require("mineflayer");

const skills = require("./lib/skillLoader");
const reflexes = require("./lib/reflexes");
const obs = require("./lib/observation/base");
const OnChat = require("./lib/observation/onChat");
const OnError = require("./lib/observation/onError");
const { Voxels, BlockRecords } = require("./lib/observation/voxels");
const Status = require("./lib/observation/status");
const Inventory = require("./lib/observation/inventory");
const OnSave = require("./lib/observation/onSave");
const OnDeath = require("./lib/observation/onDeath");
const Chests = require("./lib/observation/chests");
const { plugin: tool } = require("mineflayer-tool");

let bot = null;
let botDisconnected = false;

// end a bot on purpose, without triggering the exit on unexpected disconnects
function stopBot(target) {
    target.voyagerStopping = true;
    target.end();
}

const app = express();

app.use(bodyParser.json({ limit: "50mb" }));
app.use(bodyParser.urlencoded({ limit: "50mb", extended: false }));

app.post("/start", (req, res) => {
    if (bot) {
        bot.voyagerStopping = true;
        onDisconnect("Restarting bot");
    }
    bot = null;
    botDisconnected = false;
    console.log(req.body);
    bot = mineflayer.createBot({
        host: "localhost", // minecraft server ip
        port: req.body.port, // minecraft server port
        username: "bot",
        disableChatSigning: true,
        checkTimeoutInterval: 60 * 60 * 1000,
    });
    bot.once("error", onConnectionFailed);

    // Event subscriptions
    bot.waitTicks = req.body.waitTicks;
    // seconds a step may run before it is aborted
    bot.stepTimeout = req.body.stepTimeout || 480;
    bot.globalTickCounter = 0;
    bot.stuckTickCounter = 0;
    bot.stuckPosList = [];

    bot.on("kicked", onDisconnect);
    // after a kick or a lost connection every pending request would wait forever,
    // so exit and let the Python side restart mineflayer and rejoin
    const thisBot = bot;
    thisBot.on("end", (reason) => {
        if (thisBot.voyagerStopping) return;
        console.log(`Bot disconnected unexpectedly: ${reason}`);
        botDisconnected = true;
        // give pending responses a moment to flush
        setTimeout(() => process.exit(1), 1000);
    });

    // mounting will cause physicsTick to stop
    bot.on("mount", () => {
        bot.dismount();
    });

    bot.once("spawn", async () => {
        bot.removeListener("error", onConnectionFailed);
        if (req.body.unpause) {
            // the previous bot left the server paused
            bot.chat("/pause");
        }
        // the server keeps inventory, equipment and position across a rejoin, so nothing is restored here

        // mineflayer 4.8.1 sends window_click changedSlots as bare slot numbers, which the
        // installed protocol cannot serialize; newer mineflayer maps them to {location, item}
        const Item = require("prismarine-item")(bot.registry);
        const write = bot._client.write.bind(bot._client);
        let lastValidPosition = bot.entity.position.clone();
        bot._client.write = (name, params) => {
            // physics occasionally produces NaN, and the server kicks for invalid_player_movement;
            // drop the packet and put the bot back at its last valid position instead
            if (["position", "position_look", "look"].includes(name)) {
                const values = ["x", "y", "z", "yaw", "pitch"]
                    .filter((key) => key in params)
                    .map((key) => params[key]);
                if (!values.every(Number.isFinite)) {
                    console.log(`Dropped invalid ${name} packet: ${JSON.stringify(params)}`);
                    bot.entity.position.set(
                        lastValidPosition.x,
                        lastValidPosition.y,
                        lastValidPosition.z
                    );
                    bot.entity.velocity.set(0, 0, 0);
                    if (!Number.isFinite(bot.entity.yaw)) bot.entity.yaw = 0;
                    if (!Number.isFinite(bot.entity.pitch)) bot.entity.pitch = 0;
                    return;
                }
                if ("x" in params) {
                    lastValidPosition.set(params.x, params.y, params.z);
                }
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

        const { pathfinder } = require("mineflayer-pathfinder");
        const tool = require("mineflayer-tool").plugin;
        const collectBlock = require("mineflayer-collectblock").plugin;
        const pvp = require("mineflayer-pvp").plugin;
        const minecraftHawkEye = require("minecrafthawkeye");
        bot.loadPlugin(pathfinder);
        bot.loadPlugin(tool);
        bot.loadPlugin(collectBlock);
        bot.loadPlugin(pvp);
        bot.loadPlugin(minecraftHawkEye);

        // bot.collectBlock.movements.digCost = 0;
        // bot.collectBlock.movements.placeCost = 0;

        obs.inject(bot, [
            OnChat,
            OnError,
            Voxels,
            Status,
            Inventory,
            OnSave,
            OnDeath,
            Chests,
            BlockRecords,
        ]);
        skills.inject(bot);

        await bot.waitForTicks(bot.waitTicks);
        res.json(bot.observe());
    });

    function onConnectionFailed(e) {
        console.log(e);
        bot = null;
        res.status(400).json({ error: e });
    }
    function onDisconnect(message) {
        console.log(message);
        if (!bot) return;
        if (bot.viewer) {
            bot.viewer.close();
        }
        bot.end();
        bot = null;
    }
});

app.post("/step", async (req, res) => {
    if (!bot) {
        res.status(400).json({ error: "Bot not spawned" });
        return;
    }
    // import useful package
    let response_sent = false;
    function otherError(err) {
        console.log("Uncaught Error");
        bot.emit("error", handleError(err));
        bot.waitForTicks(bot.waitTicks).then(() => {
            if (!response_sent) {
                response_sent = true;
                // the code may still be running, so start the next step from a fresh mineflayer
                res.set("X-Voyager-Restart", "1");
                res.json(bot.observe());
            }
        });
    }

    process.on("uncaughtException", otherError);

    const mcData = require("minecraft-data")(bot.version);
    mcData.itemsByName["leather_cap"] = mcData.itemsByName["leather_helmet"];
    mcData.itemsByName["leather_tunic"] =
        mcData.itemsByName["leather_chestplate"];
    mcData.itemsByName["leather_pants"] =
        mcData.itemsByName["leather_leggings"];
    mcData.itemsByName["leather_boots"] = mcData.itemsByName["leather_boots"];
    mcData.itemsByName["lapis_lazuli_ore"] = mcData.itemsByName["lapis_ore"];
    mcData.blocksByName["lapis_lazuli_ore"] = mcData.blocksByName["lapis_ore"];
    const {
        Movements,
        goals: {
            Goal,
            GoalBlock,
            GoalNear,
            GoalXZ,
            GoalNearXZ,
            GoalY,
            GoalGetToBlock,
            GoalLookAtBlock,
            GoalBreakBlock,
            GoalCompositeAny,
            GoalCompositeAll,
            GoalInvert,
            GoalFollow,
            GoalPlaceBlock,
        },
        pathfinder,
        Move,
        ComputedPath,
        PartiallyComputedPath,
        XZCoordinates,
        XYZCoordinates,
        SafeBlock,
        GoalPlaceBlockOptions,
    } = require("mineflayer-pathfinder");
    const { Vec3 } = require("vec3");

    // Set up pathfinder
    const movements = new Movements(bot, mcData);
    bot.pathfinder.setMovements(movements);

    bot.globalTickCounter = 0;
    bot.stuckTickCounter = 0;
    bot.stuckPosList = [];
    bot.unstuckAttempts = 0;
    bot.stuckGaveUp = null;

    // the step is aborted on timeout or death; the generated code may still be running afterwards,
    // so the response asks Python to restart mineflayer (rejoin) before the next step
    let abortStep;
    const stepAborted = new Promise((resolve) => {
        abortStep = (message) => {
            const err = new Error(message);
            err.voyagerAbort = true;
            resolve(err);
        };
    });
    const deadline = setTimeout(
        () =>
            abortStep(
                `Your code ran for more than ${bot.stepTimeout} seconds and was stopped. ` +
                    "Avoid long loops and split the task into smaller steps."
            ),
        bot.stepTimeout * 1000
    );
    function onDeath(death) {
        abortStep(
            `You died: ${death.message}, at x=${death.position.x}, y=${death.position.y}, z=${death.position.z}. ` +
                "You respawned and the code was stopped. Items you carried may have dropped where you died."
        );
    }
    bot.once("voyagerDeath", onDeath);

    function onTick() {
        bot.globalTickCounter++;
        if (bot.pathfinder.isMoving()) {
            bot.stuckTickCounter++;
            if (bot.stuckTickCounter >= 100) {
                onStuck(1.5);
                bot.stuckTickCounter = 0;
            }
        }
    }

    bot.on("physicTick", onTick);
    const stopReflexes = reflexes.start(bot);

    // initialize fail count
    let _craftItemFailCount = 0;
    let _killMobFailCount = 0;
    let _mineBlockFailCount = 0;
    let _placeItemFailCount = 0;
    let _smeltItemFailCount = 0;

    // Retrieve array form post bod
    const code = req.body.code;
    const programs = req.body.programs;
    bot.cumulativeObs = [];
    await bot.waitForTicks(bot.waitTicks);
    const r = await Promise.race([evaluateCode(code, programs), stepAborted]);
    const aborted = r instanceof Error && r.voyagerAbort;
    clearTimeout(deadline);
    stopReflexes();
    bot.removeListener("voyagerDeath", onDeath);
    process.off("uncaughtException", otherError);
    if (aborted) {
        stopActions();
        bot.emit("error", r.message);
    } else if (r !== "success") {
        bot.emit("error", handleError(r));
    }
    // wait for last message
    await bot.waitForTicks(bot.waitTicks);
    if (!response_sent) {
        response_sent = true;
        if (aborted) res.set("X-Voyager-Restart", "1");
        res.json(bot.observe());
    }
    bot.removeListener("physicTick", onTick);

    function stopActions() {
        try {
            bot.pathfinder.setGoal(null);
            bot.pvp?.stop();
            bot.stopDigging();
        } catch (err) {
            console.log(`Failed to stop actions: ${err}`);
        }
        bot.clearControlStates();
    }

    async function evaluateCode(code, programs) {
        // Echo the code produced for players to see it. Don't echo when the bot code is already producing dialog or it will double echo
        try {
            await eval("(async () => {" + programs + "\n" + code + "})()");
            return "success";
        } catch (err) {
            return err;
        }
    }

    function onStuck(posThreshold) {
        const currentPos = bot.entity.position.clone();
        bot.stuckPosList.push(currentPos);

        // Check if the list is full
        if (bot.stuckPosList.length === 5) {
            const oldestPos = bot.stuckPosList[0];
            const posDifference = currentPos.distanceTo(oldestPos);

            if (posDifference < posThreshold) {
                unstuck();
            } else {
                bot.unstuckAttempts = 0;
            }

            // Remove the oldest time from the list
            bot.stuckPosList.shift();
        }
    }

    // escalate: dig out and hop while replanning, then give up on the goal so the code sees an error
    async function unstuck() {
        if (bot.unstucking) return;
        bot.unstucking = true;
        bot.unstuckAttempts++;
        const pos = bot.entity.position.floored();
        console.log(`Stuck at ${pos}, attempt ${bot.unstuckAttempts}`);
        try {
            const goal = bot.pathfinder.currentGoal;
            if (bot.unstuckAttempts <= 2 && goal) {
                await digOut(pos);
                // setting the same goal again keeps goto() pending but forces a new path
                bot.pathfinder.setGoal(goal, bot.pathfinder.currentDynamic);
                const directions = ["forward", "back", "left", "right"];
                const direction =
                    directions[Math.floor(Math.random() * directions.length)];
                bot.setControlState("jump", true);
                bot.setControlState(direction, true);
                await bot.waitForTicks(8);
                bot.setControlState("jump", false);
                bot.setControlState(direction, false);
            } else {
                bot.unstuckAttempts = 0;
                bot.stuckGaveUp =
                    `I got stuck at x=${pos.x}, y=${pos.y}, z=${pos.z} and could not reach my goal. ` +
                    "Try a different direction, or dig or pillar out of the spot first.";
                bot.chat(bot.stuckGaveUp);
                bot.pathfinder.setGoal(null);
            }
        } catch (err) {
            console.log(`Failed to get unstuck: ${err}`);
        } finally {
            bot.unstucking = false;
        }
    }

    // break solid blocks the bot is wedged in, at its feet and head and right above
    async function digOut(pos) {
        for (const dy of [0, 1, 2]) {
            const block = bot.blockAt(pos.offset(0, dy, 0));
            if (
                block &&
                block.boundingBox === "block" &&
                block.diggable &&
                bot.canDigBlock(block)
            ) {
                await bot.dig(block, true);
            }
        }
    }

    function handleError(err) {
        let stack = err.stack;
        if (!stack) {
            return err;
        }
        console.log(stack);
        const final_line = stack.split("\n")[1];
        const regex = /<anonymous>:(\d+):\d+\)/;

        const programs_length = programs.split("\n").length;
        let match_line = null;
        for (const line of stack.split("\n")) {
            const match = regex.exec(line);
            if (match) {
                const line_num = parseInt(match[1]);
                if (line_num >= programs_length) {
                    match_line = line_num - programs_length;
                    break;
                }
            }
        }
        if (!match_line) {
            return err.message;
        }
        let f_line = final_line.match(
            /\((?<file>.*):(?<line>\d+):(?<pos>\d+)\)/
        );
        if (f_line && f_line.groups && fs.existsSync(f_line.groups.file)) {
            const { file, line, pos } = f_line.groups;
            const f = fs.readFileSync(file, "utf8").split("\n");
            // let filename = file.match(/(?<=node_modules\\)(.*)/)[1];
            let source = file + `:${line}\n${f[line - 1].trim()}\n `;

            const code_source =
                "at " +
                code.split("\n")[match_line - 1].trim() +
                " in your code";
            return source + err.message + "\n" + code_source;
        } else if (
            f_line &&
            f_line.groups &&
            f_line.groups.file.includes("<anonymous>")
        ) {
            const { file, line, pos } = f_line.groups;
            let source =
                "Your code" +
                `:${match_line}\n${code.split("\n")[match_line - 1].trim()}\n `;
            let code_source = "";
            if (line < programs_length) {
                source =
                    "In your program code: " +
                    programs.split("\n")[line - 1].trim() +
                    "\n";
                code_source = `at line ${match_line}:${code
                    .split("\n")
                    [match_line - 1].trim()} in your code`;
            }
            return source + err.message + "\n" + code_source;
        }
        return err.message;
    }
});

app.post("/stop", (req, res) => {
    if (bot) stopBot(bot);
    res.json({
        message: "Bot stopped",
    });
});

app.post("/pause", (req, res) => {
    if (!bot) {
        res.status(400).json({ error: "Bot not spawned" });
        return;
    }
    // bot.chat refuses commands once the skills are injected
    (bot._chat || bot.chat)("/pause");
    // a disconnect stops ticks, so also answer when the bot ends
    const ended = new Promise((resolve) => bot.once("end", resolve));
    // a couple of ticks are enough for the command to go out, chat is only collected during steps
    Promise.race([bot.waitForTicks(2), ended]).then(() => {
        res.json({ message: "Success", disconnected: botDisconnected });
    });
});

// Server listening to PORT 3000

const DEFAULT_PORT = 3000;
const PORT = process.argv[2] || DEFAULT_PORT;
app.listen(PORT, () => {
    console.log(`Server started on port ${PORT}`);
});
