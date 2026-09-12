#include "l2mm/orderbook.hpp"

#include <openssl/evp.h>

#include <limits>
#include <stdexcept>

namespace l2mm {

Scaled parse_decimal(std::string_view text) {
    const auto dot = text.find('.');
    if (dot == std::string_view::npos || dot == 0 || text.size() - dot - 1 != 8) {
        throw std::invalid_argument("decimal must have an integer part and exactly eight fractional digits");
    }
    Scaled value = 0;
    for (std::size_t i = 0; i < text.size(); ++i) {
        if (i == dot) continue;
        const char ch = text[i];
        if (ch < '0' || ch > '9') {
            throw std::invalid_argument("decimal must contain only unsigned decimal digits and one point");
        }
        const Scaled digit = ch - '0';
        if (value > (std::numeric_limits<Scaled>::max() - digit) / 10) {
            throw std::overflow_error("scaled decimal exceeds int64 range");
        }
        value = value * 10 + digit;
    }
    return value;
}

std::string format_decimal(Scaled value) {
    if (value < 0) throw std::invalid_argument("cannot format a negative scaled value");
    const auto whole = std::to_string(value / decimal_scale);
    const auto fraction = std::to_string(value % decimal_scale);
    return whole + "." + std::string(8 - fraction.size(), '0') + fraction;
}

std::string sha256(std::string_view bytes) {
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    if (EVP_Digest(bytes.data(), bytes.size(), digest, &length, EVP_sha256(), nullptr) != 1 || length != 32) {
        throw std::runtime_error("OpenSSL SHA-256 failed");
    }
    constexpr char hex[] = "0123456789abcdef";
    std::string result;
    result.reserve(64);
    for (unsigned int i = 0; i < length; ++i) {
        result += hex[digest[i] >> 4];
        result += hex[digest[i] & 15];
    }
    return result;
}

Instrument Instrument::from_strings(std::string_view tick, std::string_view step) {
    const Instrument result{parse_decimal(tick), parse_decimal(step)};
    if (result.tick_size == 0 || result.qty_step == 0) {
        throw std::invalid_argument("tick_size and qty_step must be positive");
    }
    return result;
}

Orderbook::Orderbook(Instrument instrument) : instrument_(instrument) {
    if (instrument.tick_size <= 0 || instrument.qty_step <= 0) {
        throw std::invalid_argument("tick_size and qty_step must be positive");
    }
}

Orderbook::ParsedLevels Orderbook::validate_levels(const InputLevels& levels) const {
    ParsedLevels parsed;
    parsed.reserve(levels.size());
    for (const auto& [price_text, qty_text] : levels) {
        const Scaled price = parse_decimal(price_text);
        const Scaled qty = parse_decimal(qty_text);
        // Below 1e-6 Python Decimal switches to scientific notation. Positive
        // BTCUSDT prices and stored quantities must stay inside the fixed-eight
        // byte-parity domain. A zero quantity is only a removal/skip marker.
        if (price < 100) throw std::invalid_argument("price must be at least 0.00000100");
        if (qty != 0 && qty < 100) {
            throw std::invalid_argument("nonzero quantity must be at least 0.00000100");
        }
        if (price % instrument_.tick_size != 0) {
            throw std::invalid_argument("price is not an exact multiple of tick_size");
        }
        if (qty % instrument_.qty_step != 0) {
            throw std::invalid_argument("quantity is not an exact multiple of qty_step");
        }
        parsed.emplace_back(price, qty);
    }
    return parsed;
}

void Orderbook::apply_snapshot(const InputLevels& bids, const InputLevels& asks,
                               std::uint64_t last_update_id) {
    const auto parsed_bids = validate_levels(bids);
    const auto parsed_asks = validate_levels(asks);
    std::map<Scaled, Scaled> next_bids, next_asks;
    for (const auto& [price, qty] : parsed_bids) {
        if (qty > 0) next_bids[price] = qty;
    }
    for (const auto& [price, qty] : parsed_asks) {
        if (qty > 0) next_asks[price] = qty;
    }
    bids_.swap(next_bids);
    asks_.swap(next_asks);
    last_update_id_ = last_update_id;
    sequence_ = 0;
}

void Orderbook::apply_diff(const InputLevels& bids, const InputLevels& asks,
                           std::uint64_t last_update_id) {
    // Validate both sides before touching state, so malformed input cannot
    // leave a partially applied operation, a new update ID, or a new sequence.
    const auto parsed_bids = validate_levels(bids);
    const auto parsed_asks = validate_levels(asks);
    if (sequence_ == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("diff sequence exceeds uint64 range");
    }
    const auto apply = [](auto& side, const auto& parsed) {
        for (const auto& [price, qty] : parsed) {
            if (qty == 0) side.erase(price);
            else side[price] = qty;
        }
    };
    apply(bids_, parsed_bids);
    apply(asks_, parsed_asks);
    last_update_id_ = last_update_id;
    ++sequence_;
}

std::optional<Scaled> Orderbook::best_bid() const {
    if (bids_.empty()) return std::nullopt;
    return bids_.rbegin()->first;
}

std::optional<Scaled> Orderbook::best_ask() const {
    if (asks_.empty()) return std::nullopt;
    return asks_.begin()->first;
}

std::optional<Scaled> Orderbook::best_bid_qty() const {
    if (bids_.empty()) return std::nullopt;
    return bids_.rbegin()->second;
}

std::optional<Scaled> Orderbook::best_ask_qty() const {
    if (asks_.empty()) return std::nullopt;
    return asks_.begin()->second;
}

Scaled Orderbook::bid_quantity(Scaled price) const {
    const auto found = bids_.find(price);
    return found == bids_.end() ? 0 : found->second;
}

Scaled Orderbook::ask_quantity(Scaled price) const {
    const auto found = asks_.find(price);
    return found == asks_.end() ? 0 : found->second;
}

std::vector<std::pair<Scaled, Scaled>> Orderbook::bid_levels(std::size_t count) const {
    std::vector<std::pair<Scaled, Scaled>> result;
    for (auto it = bids_.rbegin(); it != bids_.rend() && result.size() < count; ++it) {
        result.push_back(*it);
    }
    return result;
}

std::vector<std::pair<Scaled, Scaled>> Orderbook::ask_levels(std::size_t count) const {
    std::vector<std::pair<Scaled, Scaled>> result;
    for (auto it = asks_.begin(); it != asks_.end() && result.size() < count; ++it) {
        result.push_back(*it);
    }
    return result;
}

bool Orderbook::is_crossed() const {
    const auto bid = best_bid(), ask = best_ask();
    return bid && ask && *bid >= *ask;
}

std::string Orderbook::canonical_bytes() const {
    std::string result = "{\"bids\":[";
    const auto append_levels = [&result](auto first, auto last) {
        bool separator = false;
        for (auto it = first; it != last; ++it) {
            if (separator) result += ',';
            separator = true;
            result += "[\"" + format_decimal(it->first) + "\",\"" + format_decimal(it->second) + "\"]";
        }
    };
    append_levels(bids_.rbegin(), bids_.rend());
    result += "],\"asks\":[";
    append_levels(asks_.begin(), asks_.end());
    result += "],\"last_update_id\":";
    result += last_update_id_ ? std::to_string(*last_update_id_) : "null";
    result += '}';
    return result;
}

std::string Orderbook::state_hash() const { return sha256(canonical_bytes()); }

std::string Orderbook::trace_json() const {
    const std::string canonical = canonical_bytes();
    std::string escaped;
    for (const char ch : canonical) {
        if (ch == '"' || ch == '\\') escaped += '\\';
        escaped += ch;
    }
    const auto optional_price = [](auto value) {
        return value ? "\"" + format_decimal(*value) + "\"" : std::string("null");
    };
    return "{\"canonical_bytes\":\"" + escaped + "\",\"state_hash\":\"" + sha256(canonical)
        + "\",\"best_bid\":" + optional_price(best_bid())
        + ",\"best_ask\":" + optional_price(best_ask())
        + ",\"n_bids\":" + std::to_string(bid_count())
        + ",\"n_asks\":" + std::to_string(ask_count())
        + ",\"sequence\":" + std::to_string(sequence_)
        + ",\"is_crossed\":" + (is_crossed() ? "true" : "false") + "}";
}

}  // namespace l2mm
