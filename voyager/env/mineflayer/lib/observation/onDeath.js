const Observation = require("./base.js").Observation;

class onDeath extends Observation {
    constructor(bot) {
        super(bot);
        this.name = "onDeath";
        this.obs = null;
        this.deathMessage = null;
        const ChatMessage = require("prismarine-chat")(bot.registry);
        // the server sends the death screen message, e.g. "bot was slain by Zombie"
        bot._client.on("death_combat_event", (packet) => {
            try {
                this.deathMessage = new ChatMessage(
                    JSON.parse(packet.message)
                ).toString();
            } catch (err) {
                this.deathMessage = null;
            }
        });
        bot.on("death", () => {
            const position = bot.entity.position.clone();
            // the death message can arrive just after the health update
            setTimeout(() => {
                const message = this.deathMessage || "You died";
                this.deathMessage = null;
                const death = {
                    message,
                    position: {
                        x: Math.round(position.x),
                        y: Math.round(position.y),
                        z: Math.round(position.z),
                    },
                };
                // event() consumes this.obs, so keep a copy for the step to abort with
                this.obs = death;
                this.bot.event(this.name);
                bot.emit("voyagerDeath", death);
            }, 250);
        });
    }

    observe() {
        const result = this.obs;
        this.obs = null;
        return result;
    }
}

module.exports = onDeath;
