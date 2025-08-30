DOMAIN = "stekker"
DEFAULT_BIDDING_ZONE = "10YBE----------2"  # Belgium

BIDDING_ZONES_MAP = {
    # Belgium & Netherlands
    "10YBE----------2": ["BE"],
    "10YNL----------L": ["NL"],

    # Germany & Luxembourg
    "10Y1001A1001A82H": ["DE-LU"],

    # France & Switzerland
    "10YFR-RTE------C": ["FR"],
    "10YCH-SWISSGRIDZ": ["CH"],

    # Italy regions
    "10Y1001C--00096J": ["IT-CALABRIA"],
    "10Y1001A1001A71M": ["IT-CENTRE_SOUTH"],
    "10Y1001A1001A70O": ["IT-CENTRE_NORTH"],
    "10Y1001A1001A73I": ["IT-NORTH"],
    "10Y1001A1001A885": ["IT-SACO_AC"],
    "10Y1001A1001A893": ["IT-SACO_DC"],
    "10Y1001A1001A74G": ["IT-SARDINIA"],
    "10Y1001A1001A75E": ["IT-SICILY"],
    "10Y1001A1001A788": ["IT-SOUTH"],

    # Nordics
    "10YSE-1--------K": ["SE"],
    "10YFI-1--------U": ["FI"],
    "10YNO-1--------2": ["NO1"],
    "10YNO-2--------T": ["NO2"],
    "10YNO-3--------J": ["NO3"],
    "10YNO-4--------9": ["NO4"],
    "10Y1001A1001A48H": ["NO5"],

    # Other EU
    "10YLV-1001A00074": ["LV"],
    "10YLT-1001A0008Q": ["LT"],
    "10YPL-AREA-----S": ["PL"],
    "10YPT-REN------W": ["PT"],
    "10YRO-TEL------P": ["RO"],
    "10YCS-SERBIATSOV": ["RS"],
    "10YSI-ELES-----O": ["SI"],
    "10YSK-SEPS-----K": ["SK"],
    "10YGR-HTSO-----Y": ["GR"],
    "10YHU-MAVIR----U": ["HU"],
}