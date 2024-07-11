from discord.ext import commands
from discord import app_commands
from discord.ext.commands import Context
import discord
import aiohttp
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
import os
import base64
import a2s
import datetime

load_dotenv()




class StartStopButtonSelection(discord.ui.View):
    server_options = []

    def __init__(self, server, message_context: Context, server_list: list = [], server_type: str = ""):
        super().__init__()
        self.server = server
        self.server_type = server_type
        self.server_list = server_list
        self.message_context = message_context

        for server_id in server_list:
            self.server_options.append(discord.SelectOption(label=server_id))

    @discord.ui.select(placeholder="server_id", min_values=1, max_values=1, options=server_options, custom_id="server_select")
    async def serverSelect(self, interaction: discord.Integration, select: discord.ui.Select):
        await interaction.response.defer()
        self.server_id = select.values[0]

    @discord.ui.button(label="Start", style=discord.ButtonStyle.green, custom_id="server_start")
    async def serverStartButton(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await Server.server_start(self.server, self.message_context, self.server_id, "arma3")
        
    @discord.ui.button(label="Stop", style=discord.ButtonStyle.red, custom_id="server_stop")
    async def serverStopButton(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await Server.server_stop(self.server, self.message_context, self.server_id, "arma3")


class StartStopButton(discord.ui.View):
    def __init__(self, server, message_context: Context, server_id, server_type: str = ""):
        super().__init__()
        self.server = server
        self.server_id = server_id
        self.server_type = server_type
        self.message_context = message_context

    # @discord.ui.button(label="Start", style=discord.ButtonStyle.green, custom_id="server_start")
    # async def serverStartButton(self, interaction: discord.Interaction, button: discord.ui.Button):
    #     await Server.server_start(self.server, self.message_context, self.server_id, "arma3")
        
    # @discord.ui.button(label="Stop", style=discord.ButtonStyle.red, custom_id="server_stop")
    # async def serverStopButton(self, interaction: discord.Interaction, button: discord.ui.Button):
    #     await Server.server_stop(self.server, self.message_context, self.server_id, "arma3")


class Server(commands.Cog, name="server"):
    def __init__(self, bot) -> None:
        self.bot = bot


    param_server_id = "The 'nickname' or 'id' of the server in the web panel."
    param_server_type = "The type of server that should be referenced, options are 'arma3' or 'reforger'."



    @commands.hybrid_command(
        name="server_start", 
        description="Start a specific server using the server id reference.",
    )
    @app_commands.describe(server_id=param_server_id, server_type=param_server_type)
    async def server_start(self, context: Context, server_id: str, server_type: str="arma3") -> None:
        """
        Start a specific server using the server id reference.

        :param context: The hybrid command context.
        :param server_id: The server ID which should be started.
        :param server_type: The server address which should be referenced.
        """
        
        async with aiohttp.ClientSession() as session:
            if server_id == "RNR":
                basic_auth = base64.b64encode(f"{self.bot.config['arma_server_web_admin'][server_type]['public_username']}:{self.bot.config['arma_server_web_admin'][server_type]['public_password']}".encode()).decode()
            else:
                basic_auth = await get_user_authentication(self, context.author.id, context.guild.id, server_type)
            api = f"{self.bot.config['arma_server_web_admin'][server_type]['address']}api/servers/{server_id}/start"
            async with session.post(api, headers={'Authorization': "Basic %s" % basic_auth}) as response:
                if response.status == 200:
                    embed = discord.Embed(title="Server Started", description=f"The server (`{server_id}`) has been started.", color=0xBEBEFE)
                    embed.set_footer(text=f"{api}")
                    await context.send(embed=embed, ephemeral=True)
                elif response.status == 401:
                    embed.set_footer(text=f"{api}")
                    await context.send(embed=embed, ephemeral=True)
                elif response.status == 500:
                    embed = await server_not_exist_embed(self, server_id)
                    embed.set_footer(text=f"{api}")
                    await context.send(embed=embed, ephemeral=True)
                else:
                    embed = await error_embed(self, response)
                    embed.set_footer(text=f"{api}")
                    await context.send(embed=embed, ephemeral=True)



    @commands.hybrid_command(
        name="server_stop", 
        description=" Stop a specific server using the server id reference.",
    )
    @app_commands.describe(server_id=param_server_id, server_type=param_server_type)
    async def server_stop(self, context: Context, server_id: str, server_type: str="arma3") -> None:
        """
        Stop a specific server using the server id reference.

        :param context: The hybrid command context.
        :param server_id: The server which should be started.
        """

        async with aiohttp.ClientSession() as session:
            basic_auth = await get_user_authentication(self, context.author.id, context.guild.id, server_type)
            api = f"{self.bot.config['arma_server_web_admin'][server_type]['address']}api/servers/{server_id}/stop"
            async with session.post(api, headers={'Authorization': "Basic %s" % basic_auth}) as response:
                if response.status == 200:
                    embed = discord.Embed(title="Server Stopped", description=f"The server (`{server_id}`) has been stopped.", color=0xBEBEFE)
                    embed.set_footer(text=f"{api}")
                    await context.send(embed=embed, ephemeral=True)
                elif response.status == 401:
                    embed = await unauthorised_embed(self)
                    embed.set_footer(text=f"{api}")
                    await context.send(embed=embed, ephemeral=True)
                elif response.status == 500:
                    embed = await server_not_exist_embed(self, server_id)
                    embed.set_footer(text=f"{api}")
                    await context.send(embed=embed, ephemeral=True)
                else:
                    embed = await error_embed(self, response)
                    embed.set_footer(text=f"{api}")
                    await context.send(embed=embed, ephemeral=True)
    


    @commands.hybrid_command(
        name="server_upload", 
        description="Upload a .pbo mission file directly to the server.",
    )
    @app_commands.describe(mission_pbo="The .pbo file for mission you wish to upload to the server.", server_type=param_server_type)
    async def server_mission_upload(self, context: Context, mission_pbo: discord.Attachment, server_type: str="arma3") -> None:
        """
        Upload a .pbo mission file directly to the server.

        :param context: The hybrid command context.
        :param mission_pbo: The server which should be started.
        """

        pass

    @commands.hybrid_command(
        name="server_list", 
        description="List the available server_ids on the web panel.",
    )
    @app_commands.describe(server_type=param_server_type)
    async def server_list(self, context: Context, server_type: str="arma3") -> None:
        """
        List the available server IDs to be used in other commands.

        :param context: The hybrid command context.
        """

        async with aiohttp.ClientSession() as session:
            basic_auth = await get_user_authentication(self, context.author.id, context.guild.id, server_type)
            # print(basic_auth, server_type)
            api = f"{self.bot.config['arma_server_web_admin'][server_type]['address']}api/servers/"
            async with session.get(api, headers={'Authorization': "Basic %s" % basic_auth}) as response:
                if response.status == 200:
                    server_list = []
                    server_ids = ""
                    server_names = ""
                    server_ports = ""
                    for server in await response.json():
                        server_list.append(server["uid"])
                        server_ids = server_ids + "`" + server["uid"]  + "`" + "\n"
                        server_names = server_names + "`"  + server["title"] + "`" + "\n"
                        server_ports = server_ports + "`"  + str(server["port"]) + "`" + "\n"
                    embed = discord.Embed(title="Available Servers", description="", color=0xBEBEFE)
                    embed.add_field(name=f"ID", value=server_ids, inline=True)
                    embed.add_field(name=f"Name", value=server_names, inline=True)
                    embed.add_field(name=f"Port", value=server_ports, inline=True)
                    await context.send(embed=embed, ephemeral=True, view=StartStopButtonSelection(self, message_context=context, server_type=server_type, server_list=server_list))
                elif response.status == 401:
                    embed = await unauthorised_embed(self)
                    embed.set_footer(text=f"{api}")
                    await context.send(embed=embed, ephemeral=True)
                else:
                    embed = await error_embed(self, response)
                    embed.set_footer(text=f"{api}")
                    await context.send(embed=embed, ephemeral=True)



    @commands.hybrid_command(
        name="server_status", 
        description="Get the current server status of a specific server_id or setup a info message for the public to see.",
    )
    @app_commands.describe(server_id=param_server_id, server_type=param_server_type, status_channel="Setup a auto status message in a specified channel.",server_ip="Override the IP to point at a specific address rather than localhost.", server_description="Any additional information you want to include in the server status.", server_modpack="A link to the modpack used for the server.")
    async def server_status(self, context: Context, server_id: str, server_type: str="arma3", status_channel: discord.TextChannel=None, message_id: str="", server_ip: str="am2.taw.net", server_description: str="", server_modpack: str="") -> None:
        """
        Get the current server status of a specific server_id or setup a info message for the public to see.

        :param context: The hybrid command context.
        """
        async with aiohttp.ClientSession() as session:
            #Get user authentication from the data base
            basic_auth = await get_user_authentication(self, context.author.id, context.guild.id, server_type)
            api = f"{self.bot.config['arma_server_web_admin'][server_type]['address']}api/servers/{server_id}"
            #Get the server information from the server_id
            async with session.get(api, headers={'Authorization': "Basic %s" % basic_auth}) as response:
                #Successful response and server config recieved.
                #print(response.status, response.reason, response.content)
                if response.status == 200:
                    server = await response.json()
                    if not server is None:
                        status_embed = await self.bot.server_status_embed(server_id, server_type, server_ip, server['port'], server["title"], server_description, server_modpack)
                        if status_channel: #Add status message to database to keep track.
                            if not message_id:
                                message = await status_channel.send("Querying the servers....")
                                message_id = message.id
                            response = await self.bot.database.add_server_status(context.guild.id, status_channel.id, message_id, context.author.id, server_id, server_type, server_ip, server['port'], server["title"], server_description, server_modpack)
                            if response:
                                await context.send(f"The status message ({message_id}) has been setup in {status_channel} sucessfully, sending update...", ephemeral=True)
                            #This update takes too long to send back to the interaction, can maybe call this back/defer. One for later maybe.
                            await self.bot.update_server_status_message(context.guild.id, status_channel.id, message_id)
                        else: #Just post current snapshot back to context
                            await context.send(embed=status_embed)
                    else: #ID/Nickname could not be found.
                            status_embed = await server_not_exist_embed(self, server_id)
                            await context.send(embed=status_embed, ephemeral=True)
                elif response.status == 401:
                    status_embed = await unauthorised_embed(self)
                    status_embed.set_footer(text=f"{api}")
                    await context.send(embed=status_embed, ephemeral=True)
                else:
                    status_embed = await error_embed(self, response)
                    status_embed.set_footer(text=f"{api}")
                    await context.send(embed=status_embed, ephemeral=True)



    @commands.hybrid_command(
        name="server_login", 
        description="Setup your web panel information in discord so you can use the server commands.",
    )
    @app_commands.describe(server_type=param_server_type)
    async def server_login(self, context: Context, username: str, password: str, server_type: str="arma3") -> None:
        """
        Setup your web panel information in discord so you can use the discord commands to interact with the servers directly.

        :param context: The hybrid command context.
        """
        # member = context.guild.get_member(user.id) or await context.guild.fetch_member(
        #     user.id
        # )
        user_id = context.author.id
        guild_id = context.guild.id
        response = await self.bot.database.set_server_authentication(user_id, guild_id, server_type, username, password)
        if response:
            embed = discord.Embed(description=f"Your webpanel authentication has been setup with {user_id}, {guild_id}, {server_type}, {username}, {password}.", color=0xBEBEFE)
        else:
            embed = discord.Embed(title=f"Error", description=f"""Please try again or contact the server administrator.""", color=0xE02B2B)
        await context.send(embed=embed, ephemeral=True)

async def get_user_authentication(self, user_id: str, guild_id: str, server_type: str="arma3") -> None:
    authentication = await self.bot.database.get_server_authentication(user_id, guild_id, server_type)
    # print(authentication, base64.b64encode(f"{authentication['username']}:{authentication['password']}".encode()).decode())
    return base64.b64encode(f"{authentication['username']}:{authentication['password']}".encode()).decode()


async def unauthorised_embed(self):
    return discord.Embed(
        title=f"Unauthorised Access",
        description=f"""Your login details for the web panel are either incorrect or you do not have the required permissions.
        You can use /server_login [username] [password] to set up your details to use this bot.
        """,
        color=0xE02B2B,
    )

async def error_embed(self, response):
    return discord.Embed(
        title=f"{response.reason} ({response.status})",
        description=f"{response.content}",
        color=0xE02B2B,
    )
    
async def server_not_exist_embed(self, server_id=""):
    return discord.Embed(
        title=f"`{server_id}` Not Found",
        description=f"Use /server_list to see the available servers.",
        color=0xE02B2B,
    )
    


async def setup(bot) -> None:
    await bot.add_cog(Server(bot))
