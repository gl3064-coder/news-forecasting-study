"""Hand-labeled evaluation set for detect_tier.

80 messages drawn at random (seed 20260727) from news_corpus.db and labeled by
reading the subject plus the first ~300 characters of the body.

Labeling rules used, so the set can be re-checked or extended consistently:
  geopolitical - war, diplomacy, sanctions, and NATIONAL/INTERNATIONAL politics
  finance      - markets, the Fed, rates, earnings, deals, credit, oil as a price
  lifestyle    - food, shopping/product picks, style, sports, travel, culture, health tips
  mixed        - general digests with no dominant theme, local NYC news, tech/science features

Judgment calls worth knowing about:
  - Local NYC politics (N.Y. Today) is 'mixed', not 'geopolitical'.
  - Sanctions-evasion stories (Iran/Binance) are 'geopolitical', not 'finance'.
  - War-driven oil moves are 'geopolitical' when the war leads, 'finance' when the
    price/market reaction leads.
  - 'mixed' is the fuzziest label; per-tier precision on the other three is the
    metric that actually matters.
"""

LABELS: dict[str, str] = {
    "19e4f3415687aeed": "geopolitical",  # 10-Point: crypto network funding Iran's regime
    "19c9bc0b3ee3057f": "lifestyle",     # Where to Eat: and have a shvitz
    "19aa69cdfc4e1c12": "lifestyle",     # best Black Friday deals
    "19ef42f26c98e6ca": "geopolitical",  # WSJ Politics: Trump's Iran war aims
    "19dce7c59395f92b": "finance",       # Markets A.M.: don't get greedy with AI stocks
    "19b43927d223f6f8": "lifestyle",     # best last-minute gifts
    "19e6e23229fb5d2d": "finance",       # Wealth Adviser: Ford stock surge, ECB
    "19a768b62feaec3d": "geopolitical",  # The World: female leaders on the right
    "19b42e2dbb6b06fc": "mixed",         # For You: how Epstein got rich
    "19dc49f4fd91f46b": "lifestyle",     # how to protect yourself from ticks
    "19b0d804a87521c1": "lifestyle",     # 2025's most stylish people
    "19e8693df3060958": "geopolitical",  # The World: Ebola outbreak, Lebanon cease-fire
    "19b9a956185837ff": "lifestyle",     # drafty window fix
    "19b5adb63ca4df60": "lifestyle",     # our favorite vacuums
    "19d6988771c27272": "lifestyle",     # Where to Eat: best dishes in March
    "19d2ee3205e52571": "finance",       # Markets A.M.: private credit, Nasdaq correction
    "19b0e0a6f48381f5": "mixed",         # Breaking news: Disney licenses to OpenAI
    "19f6c8428c27f9dc": "lifestyle",     # Where to Eat: three styles of mango
    "19df245c9f982682": "lifestyle",     # N.Y. Today: the Met Gala is here
    "19d07d24b368135f": "mixed",         # For You: Cesar Chavez accusations
    "19c098d0848afdd8": "geopolitical",  # The Morning: a crisis for Trump, shutdown
    "19d75aa2487efe0a": "geopolitical",  # The World: how Iranians feel now
    "19daf800e0b70ea6": "finance",       # 10-Point: Warsh before Senate Banking, rate cuts
    "19cab429c39b883b": "lifestyle",     # these sports increase longevity
    "19c9bea4712300a3": "geopolitical",  # For You: Epstein files, Israel/Iran war
    "19ab2ab9874e04b5": "geopolitical",  # For You: Trump shows his power
    "19f5b185089ca522": "finance",       # Markets A.M.: media deals, Hormuz
    "19e9740198c2b5de": "finance",       # 10-Point: SpaceX millionaires, private credit
    "19e7e8e3492c11a3": "mixed",         # Tokenmaxxing Maxes Out (AI newsletter)
    "19f7f15fb5533396": "finance",       # Wealth Adviser: private-equity riches, Fed
    "19e4a39eea4734a8": "finance",       # Risk Report: suspicious oil trades probe
    "19dddf20f2f5fe4c": "geopolitical",  # The Morning: Hegseth on the Iran war
    "19f67b977ad7db1f": "lifestyle",     # new favorite air purifier
    "19df8e3b7b53d705": "finance",       # AI's rising costs, capex, Meta borrowing
    "19bbc012eec58fcd": "mixed",         # N.Y. Today: new schools chancellor
    "19c77b43f5569452": "lifestyle",     # Where to Eat: Ask Becky
    "19b3343ae64a128b": "lifestyle",     # Where to Eat: the Eaties
    "19d300bf3e8fa704": "mixed",         # quest to revive a frozen brain
    "19d1a48736793b4b": "geopolitical",  # The Morning: Israel pounding Tehran
    "19d192ccc576ea82": "geopolitical",  # The World: Trump's ultimatum
    "19da5b9a45661aae": "lifestyle",     # the perfect huggie earrings
    "19b9d7659721719e": "geopolitical",  # The Morning: Oval Office, Venezuela
    "19ea29c5aa245529": "mixed",         # Google's data-center gambit (tech)
    "19d2f0228d9d23f2": "finance",       # Risk Report: FTC warns payment processors
    "19e756e0470dc87e": "finance",       # Dividing the Pie / Markets P.M.
    "19de85d9926452cc": "finance",       # 10-Point: how Spirit's rescue unraveled
    "19f654e7a9ce112a": "geopolitical",  # 10-Point: U.S. and Iran race against the clock
    "19da59d7b3f7312b": "lifestyle",     # should you wash new clothes
    "19f1cf781d44bd07": "mixed",         # N.Y. Today: Hillary Clinton at Carnegie Hall
    "19f4f7952bac37ca": "mixed",         # The World: five stories you missed (microbiome)
    "19cc45c8757a90b7": "lifestyle",     # Wirecutter: $300 calendar
    "19ed90cc747092ab": "geopolitical",  # The World: teenagers vs social media bans
    "19df14d07c6a5bab": "geopolitical",  # The World: DeepSeek's sequel
    "19cd9561fbc676af": "lifestyle",     # Where to Eat: Giancarlo Esposito
    "19e7581243131d40": "geopolitical",  # For You: Abraham Accords, Russian drone
    "19d05b70ba84db54": "geopolitical",  # The Morning: energy targets in the war
    "19f95e3653531ab4": "geopolitical",  # For You: Trump backs Graham's sister
    "19df9e39afa9e570": "mixed",         # For You from Opinion: rich people, LG rift
    "19eeeed7ac217714": "finance",       # Markets A.M.: Strategy's chairman, oil
    "19d4991b63d84642": "mixed",         # why work is so joyless (careers)
    "19db4e564d08872e": "geopolitical",  # WSJ Politics: D.C.'s bad week
    "19df7b146d58ddd8": "finance",       # Wealth Adviser: bond ETFs, energy shock
    "19e3b9b32ba440c9": "lifestyle",     # In Short: Instagram, romance novels
    "19f1d3b656e5587a": "finance",       # Wealth Adviser: high stock prices, JPMorgan
    "19b085a954634383": "lifestyle",     # advice column: fish out of water
    "19a5383f601a6f36": "mixed",         # N.Y. Today: Mamdani's coalition
    "19e63f98664ba4c9": "geopolitical",  # Risk Report: Iran moved billions via Binance
    "19d4fc85354c88af": "lifestyle",     # Where to Eat: restaurant dupe
    "19eb1189db08b8cc": "finance",       # Markets A.M.: inflation picking pockets
    "19ba7b0d18c70f36": "geopolitical",  # N.Y. Today: protecting Maduro at trial
    "19e8d09d45fbd2d6": "finance",       # Wealth Adviser: Alphabet, private credit
    "19e25f400b537233": "finance",       # 10-Point: AI's golden age payoff, hedge funds
    "19be59ed0bd73723": "geopolitical",  # The Morning: Trump takes Davos
    "19d4374a9f5718a4": "finance",       # Wealth Adviser: Fed cuts, tariffs, inflation
    "19ade8fc77fcfbf2": "mixed",         # N.Y. Today: the B.Q.E. headache
    "19d3978c95638d03": "mixed",         # turmoil at RFK Jr.'s C.D.C.
    "19dfc925236eb90a": "mixed",         # N.Y. Today: fare evasion
    "19eae203287ecbda": "geopolitical",  # For You: conspiracy probe rocked Justice Dept
    "19cb858adc2a2110": "mixed",         # N.Y. Today: A.I. in the classroom
    "19f6b71cdafaa107": "lifestyle",     # In Short: World Cup, Twitter, meteorite
}
