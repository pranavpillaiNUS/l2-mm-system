#pragma once

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace l2mm {

using Scaled = std::int64_t;
using InputLevels = std::vector<std::pair<std::string, std::string>>;
constexpr Scaled decimal_scale = 100000000;

// Strict, nonnegative fixed-eight decimal input; never passes through double.
Scaled parse_decimal(std::string_view text);
std::string format_decimal(Scaled value);
std::string sha256(std::string_view bytes);

struct Instrument {
    Scaled tick_size;
    Scaled qty_step;
    static Instrument from_strings(std::string_view tick, std::string_view step);
};

class Orderbook {
public:
    explicit Orderbook(Instrument instrument);
    void apply_snapshot(const InputLevels& bids, const InputLevels& asks,
                        std::uint64_t last_update_id);
    void apply_diff(const InputLevels& bids, const InputLevels& asks,
                    std::uint64_t last_update_id);

    std::optional<Scaled> best_bid() const;
    std::optional<Scaled> best_ask() const;
    std::optional<Scaled> best_bid_qty() const;
    std::optional<Scaled> best_ask_qty() const;
    Scaled bid_quantity(Scaled price) const;
    Scaled ask_quantity(Scaled price) const;
    std::vector<std::pair<Scaled, Scaled>> bid_levels(std::size_t count) const;
    std::vector<std::pair<Scaled, Scaled>> ask_levels(std::size_t count) const;
    std::size_t bid_count() const { return bids_.size(); }
    std::size_t ask_count() const { return asks_.size(); }
    std::optional<std::uint64_t> last_update_id() const { return last_update_id_; }
    std::uint64_t sequence() const { return sequence_; }
    bool is_crossed() const;
    std::string canonical_bytes() const;
    std::string state_hash() const;
    std::string trace_json() const;

private:
    using ParsedLevels = std::vector<std::pair<Scaled, Scaled>>;
    ParsedLevels validate_levels(const InputLevels& levels) const;
    Instrument instrument_;
    std::map<Scaled, Scaled> bids_;
    std::map<Scaled, Scaled> asks_;
    std::optional<std::uint64_t> last_update_id_;
    std::uint64_t sequence_ = 0;
};

}  // namespace l2mm
