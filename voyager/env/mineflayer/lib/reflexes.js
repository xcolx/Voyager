// survival reflexes that run while generated code executes, so the bot does not starve, drown or
// stand still while being hit. They only act when the bot is not busy pathing, digging or fighting.

const FOODS = [
    "cooked_beef",
    "cooked_porkchop",
    "cooked_mutton",
    "cooked_chicken",
    "cooked_rabbit",
    "cooked_cod",
    "cooked_salmon",
    "baked_potato",
    "bread",
    "carrot",
    "apple",
    "beef",
    "porkchop",
    "mutton",
    "rabbit",
    "potato",
    "beetroot",
    "sweet_berries",
    "melon_slice",
    "cookie",
    "dried_kelp",
];

function start(bot) {
    let busy = false;
    let surfacing = false;
    let ticks = 0;

    function idle() {
        return (
            !busy &&
            !bot.pathfinder.isMoving() &&
            !bot.pathfinder.isMining() &&
            !bot.targetDigBlock &&
            !bot.pvp?.target
        );
    }

    async function eat() {
        const food = FOODS.map((name) =>
            bot.inventory.items().find((item) => item.name === name)
        ).find(Boolean);
        if (!food) return;
        busy = true;
        const held = bot.heldItem;
        try {
            await bot.equip(food, "hand");
            await bot.consume();
            bot.chat(`I ate ${food.name} because I was hungry.`);
            if (held) await bot.equip(held, "hand");
        } catch (err) {
            console.log(`Reflex eat failed: ${err}`);
        } finally {
            busy = false;
        }
    }

    function onTick() {
        ticks++;
        // surface when running out of air, even mid path
        if (bot.entity.isInWater && bot.oxygenLevel < 8) {
            surfacing = true;
            bot.setControlState("jump", true);
            return;
        }
        if (surfacing) {
            surfacing = false;
            bot.setControlState("jump", false);
        }
        if (ticks % 20 !== 0 || !idle()) return;
        if (bot.food <= 6) eat();
    }

    function onHurt(entity) {
        if (entity !== bot.entity || !idle()) return;
        const attacker = bot.nearestEntity(
            (e) =>
                e.kind === "Hostile mobs" &&
                e.position.distanceTo(bot.entity.position) < 3.5
        );
        if (attacker) {
            bot.attack(attacker);
        }
    }

    bot.on("physicTick", onTick);
    bot.on("entityHurt", onHurt);
    return () => {
        if (surfacing) bot.setControlState("jump", false);
        bot.removeListener("physicTick", onTick);
        bot.removeListener("entityHurt", onHurt);
    };
}

module.exports = { start };
