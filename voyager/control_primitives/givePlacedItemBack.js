async function givePlacedItemBack(bot, name, position) {
    // mine the blocks placed during a failed attempt and pick them up again
    for (let i = 0; i < name.length; i++) {
        await givePlacedItemBackSingle(bot, name[i], position[i]);
    }

    async function givePlacedItemBackSingle(bot, name, position) {
        const x = Math.floor(position.x);
        const y = Math.floor(position.y);
        const z = Math.floor(position.z);
        // the reported position can be slightly off, search a small cube around it
        const size = 3;
        for (let dx = -size; dx <= size; dx++) {
            for (let dy = -size; dy <= size; dy++) {
                for (let dz = -size; dz <= size; dz++) {
                    const block = bot.blockAt(new Vec3(x + dx, y + dy, z + dz));
                    if (block?.name === name) {
                        try {
                            await bot.collectBlock.collect(block);
                        } catch (err) {
                            console.log(`Failed to take back ${name}: ${err}`);
                        }
                        return;
                    }
                }
            }
        }
    }
}
